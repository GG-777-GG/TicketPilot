"""
文件职责：验证审计保存、历史快照及业务事务共同提交/回滚。
作用与配合：真实Repository和临时SQLite，直接观察数据库状态。
边界：不把事务示例当成业务Service已经接入审计，不操作真实业务库。
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.audit import repository
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.audit.schemas import AuditEventInput
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def audit_context(database_session):
    """先提交用户和工单，便于独立验证后续事务。"""
    user = build_user(role="agent")
    ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com", channel="web",
                          subject="事务样本", description="审计测试", priority="medium", status="open")
    database_session.add_all([user, ticket])
    database_session.commit()
    event = AuditEventInput(event_type="ticket.closed", outcome="succeeded", actor_id=user.id, actor_role=user.role,
                            ticket_id=ticket.id, approval_id=uuid4(), operation_id=uuid4(), request_id=uuid4(), trace_id="b" * 32)
    return user, ticket, event


def event_count(session):
    """读取当前事务可见事件数量。"""
    return session.scalar(select(func.count()).select_from(AuditEventRecord))


def test_add_flushes_and_caller_commit_persists(database_session, audit_context):
    """事件与业务变更一同提交，独立Session能查到全部结果。"""
    user, ticket, event = audit_context
    ticket.status = "closed"
    record = repository.add_event(database_session, event)
    assert UUID(record.id) and record.occurred_at is not None
    database_session.commit()
    with Session(bind=database_session.get_bind()) as verification:
        stored = verification.get(AuditEventRecord, record.id)
        assert stored.actor_id == user.id and stored.actor_role == "agent"
        assert stored.operation_id == str(event.operation_id) and stored.trace_id == event.trace_id
        assert verification.get(TicketRecord, ticket.id).status == "closed"


def test_add_does_not_commit_and_caller_can_rollback(database_session, audit_context):
    """flush已生效仍能撤销，防止Repository提前提交业务事务。"""
    _, ticket, event = audit_context
    ticket.status = "closed"
    repository.add_event(database_session, event)
    assert event_count(database_session) == 1
    database_session.rollback()
    with Session(bind=database_session.get_bind()) as verification:
        assert event_count(verification) == 0
        assert verification.get(TicketRecord, ticket.id).status == "open"


def test_error_after_actual_flush_can_roll_back_both(database_session, audit_context):
    """审计与工单实际flush后发生错误，调用方回滚两种写入。"""
    _, ticket, event = audit_context
    try:
        ticket.status = "closed"
        repository.add_event(database_session, event)
        assert event_count(database_session) == 1
        raise SQLAlchemyError("模拟提交前错误")
    except SQLAlchemyError:
        database_session.rollback()
    assert event_count(database_session) == 0
    database_session.refresh(ticket)
    assert ticket.status == "open"


def test_repository_propagates_flush_error_without_rollback(database_session, audit_context, monkeypatch):
    """记录层不吞异常或自行回滚，交给业务Service处理整个事务。"""
    _, _, event = audit_context
    error = SQLAlchemyError("测试flush失败")
    def fail_flush():
        """模拟数据库错误。"""
        raise error
    def unexpected_rollback():
        """Repository不拥有回滚决定权。"""
        raise AssertionError("记录层不应自行rollback")
    with monkeypatch.context() as patch:
        patch.setattr(database_session, "flush", fail_flush)
        patch.setattr(database_session, "rollback", unexpected_rollback)
        with pytest.raises(SQLAlchemyError) as captured:
            repository.add_event(database_session, event)
    assert captured.value is error
    database_session.rollback()
    assert event_count(database_session) == 0


def test_history_survives_role_change_and_parent_deletion(database_session, audit_context):
    """已提交事件保留原角色与目标编号，删除原对象不会级联删除历史。"""
    user, ticket, event = audit_context
    actor_id, ticket_id = user.id, ticket.id
    record = repository.add_event(database_session, event)
    database_session.commit()
    user.role = "admin"
    database_session.commit()
    database_session.delete(user)
    database_session.delete(ticket)
    database_session.commit()
    database_session.expire_all()
    stored = database_session.get(AuditEventRecord, record.id)
    assert stored.actor_id == actor_id and stored.actor_role == "agent" and stored.ticket_id == ticket_id


def test_same_operation_can_have_execution_and_replay_events(database_session, audit_context):
    """一个操作可关联多条事件，事件ID独立，不把operation_id当审计主键。"""
    _, _, event = audit_context
    first = repository.add_event(database_session, event)
    replay = AuditEventInput.model_validate({**event.model_dump(), "event_type": "execution.replayed", "outcome": "replayed"})
    second = repository.add_event(database_session, replay)
    database_session.commit()
    assert first.id != second.id and first.operation_id == second.operation_id
    assert event_count(database_session) == 2


@pytest.mark.parametrize("outcome", [None, "invalid"])
def test_database_rejects_missing_or_invalid_outcome(database_session, outcome):
    """绕过Pydantic直接构造ORM时，数据库仍保护结果必填和合法值。"""
    database_session.add(AuditEventRecord(id=str(uuid4()), event_type="approval.failed", outcome=outcome))
    with pytest.raises(IntegrityError):
        database_session.flush()
    database_session.rollback()
    assert event_count(database_session) == 0
