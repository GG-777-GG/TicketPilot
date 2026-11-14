"""
文件职责：验证失败审计的独立事务、资源释放和异常处理。
作用与配合：真实审计Service/Repository，使用临时文件SQLite和新会话。
边界：不访问业务库，不表示审批失败分支或HTTP权限依赖已接入审计。
"""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ticketpilot.audit import repository, service
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.audit.schemas import AuditEventInput
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def audit_database(tmp_path):
    """文件库让独立连接可验证真实提交；追踪Service会话是否关闭。"""
    engine = create_engine("sqlite:///" + (tmp_path / "audit.sqlite").as_posix())
    AuditEventRecord.__table__.create(engine)
    TicketRecord.__table__.create(engine)
    sessions = []

    class TrackedSession(Session):
        """只记录生命周期，不替换真实事务行为。"""

        def __init__(self, *args, **kwargs):
            """保留每个新Session供测试检查。"""
            super().__init__(*args, **kwargs)
            self.was_closed = False
            sessions.append(self)

        def close(self):
            """记录关闭并调用真实资源清理。"""
            self.was_closed = True
            super().close()

    factory = sessionmaker(engine, class_=TrackedSession, expire_on_commit=False)
    try:
        yield engine, factory, sessions
    finally:
        engine.dispose()


def failed_event(outcome="failed"):
    """构造可信后端事件，不包含原始异常正文。"""
    return AuditEventInput(
        event_type="approval.denied" if outcome == "denied" else "approval.failed",
        outcome=outcome,
        error_code="permission_denied" if outcome == "denied" else "storage_error",
        actor_id=uuid4(), actor_role="agent", approval_id=uuid4(),
        ticket_id=uuid4(), operation_id=uuid4(),
    )


def stored_events(engine):
    """使用全新Session验证已提交数据，不依赖Service身份映射。"""
    with Session(engine) as check:
        return list(check.scalars(select(AuditEventRecord)))


@pytest.mark.parametrize("outcome", ["denied", "failed"])
def test_independent_commit_persists_event_and_closes_session(audit_database, outcome):
    """函数返回True时数据已提交，工厂创建的Session已关闭。"""
    engine, factory, sessions = audit_database
    value = failed_event(outcome)
    assert service.record_failed_attempt(event=value, session_factory=factory) is True
    saved, = stored_events(engine)
    for field, expected in value.model_dump(mode="json").items():
        assert getattr(saved, field) == expected
    assert saved.id and saved.occurred_at is not None
    assert len(sessions) == 1 and sessions[0].was_closed


def test_business_rollback_then_audit_survives_later_rollback(audit_database):
    """原业务实际flush后回滚；独立审计保留，后续业务回滚不影响它。"""
    engine, factory, _ = audit_database
    ticket_id = str(uuid4())
    with Session(engine) as business:
        ticket = TicketRecord(id=ticket_id, customer_name="测试", contact_email="test@example.com",
                              channel="web", subject="失败事务", description="测试", priority="medium", status="open")
        business.add(ticket)
        business.commit()
        ticket.status = "closed"
        business.flush()
        business.rollback()
        assert service.record_failed_attempt(event=failed_event(), session_factory=factory) is True
        business.rollback()
    assert len(stored_events(engine)) == 1
    with Session(engine) as check:
        assert check.get(TicketRecord, ticket_id).status == "open"


@pytest.mark.parametrize("outcome", ["succeeded", "replayed", "denied", "failed"])
def test_invalid_input_does_not_open_session(audit_database, outcome):
    """成功结果或缺失错误类别应明确报参数错误，不能悄悄返回False。"""
    engine, factory, sessions = audit_database
    value = AuditEventInput(event_type="approval.failed", outcome=outcome)
    with pytest.raises(ValueError):
        service.record_failed_attempt(event=value, session_factory=factory)
    assert sessions == [] and stored_events(engine) == []


@pytest.mark.parametrize("failure", ["after_flush", "commit"])
def test_database_error_rolls_back_closes_and_logs_only_fixed_message(audit_database, monkeypatch, caplog, failure):
    """真实写入后异常及提交异常均不留事件，不记录异常敏感正文。"""
    engine, factory, sessions = audit_database
    original_add = repository.add_event

    def broken_add(session, value):
        """实际flush之后抛出数据库错误。"""
        original_add(session, value)
        raise SQLAlchemyError("private-test-marker")

    def broken_commit(session):
        """模拟尚未提交成功时数据库报错。"""
        raise SQLAlchemyError("private-test-marker")

    if failure == "after_flush":
        monkeypatch.setattr(repository, "add_event", broken_add)
    else:
        sqlalchemy_event.listen(factory, "before_commit", broken_commit)
    assert service.record_failed_attempt(event=failed_event(), session_factory=factory) is False
    assert stored_events(engine) == []
    assert len(sessions) == 1 and sessions[0].was_closed
    records = [item for item in caplog.records if item.name == service.__name__]
    assert len(records) == 1 and records[0].getMessage() == "失败审计未能保存"
    assert records[0].exc_info is None and "private-test-marker" not in caplog.text


def test_factory_database_failure_returns_false(audit_database, monkeypatch):
    """创建审计会话阶段的数据库异常也遵守相同返回约定。"""
    engine, factory, sessions = audit_database

    def broken_begin():
        """模拟工厂无法开始事务。"""
        raise SQLAlchemyError("测试数据库不可用")

    monkeypatch.setattr(factory, "begin", broken_begin)
    assert service.record_failed_attempt(event=failed_event(), session_factory=factory) is False
    assert sessions == [] and stored_events(engine) == []


def test_programming_error_is_not_silently_hidden(audit_database, monkeypatch):
    """非数据库编程错误继续传播，同时上下文仍回滚并关闭Session。"""
    engine, factory, sessions = audit_database
    original_add = repository.add_event

    def broken_add(session, value):
        """写入后模拟编程错误，验证不能误报为普通保存失败。"""
        original_add(session, value)
        raise TypeError("测试编程错误")

    monkeypatch.setattr(repository, "add_event", broken_add)
    with pytest.raises(TypeError, match="测试编程错误"):
        service.record_failed_attempt(event=failed_event(), session_factory=factory)
    assert stored_events(engine) == [] and sessions[0].was_closed


def test_caller_can_preserve_original_business_error(audit_database, monkeypatch):
    """示范调用方保留原异常；这不是正式审批失败分支已经接入的证明。"""
    _, factory, _ = audit_database
    original_error = RuntimeError("原业务失败")

    def broken_add(session, value):
        """模拟审计数据库写入失败。"""
        raise SQLAlchemyError("模拟审计失败")

    monkeypatch.setattr(repository, "add_event", broken_add)
    with pytest.raises(RuntimeError) as captured:
        try:
            raise original_error
        except RuntimeError:
            assert service.record_failed_attempt(event=failed_event(), session_factory=factory) is False
            raise
    assert captured.value is original_error
