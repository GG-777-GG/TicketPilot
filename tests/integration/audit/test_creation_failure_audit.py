"""
文件职责：验证创建、审批决定和执行失败时的独立审计及原异常保留。
作用与配合：真实审批/执行/失败审计Service，使用临时文件SQLite。
边界：不代表工作流外层或HTTP认证依赖失败已接入审计。
"""

from types import SimpleNamespace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from tests.factories.identity import build_user
from ticketpilot.approvals import execution, repository, service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalCreateRequest, ApprovalReviewRequest
from ticketpilot.audit import repository as audit_repository
from ticketpilot.audit import service as audit_service
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.core.database import Base
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def case(tmp_path):
    """业务和独立审计使用同一个文件数据库，通过新Session确认提交。"""
    engine = create_engine("sqlite:///" + (tmp_path / "creation.sqlite").as_posix(), hide_parameters=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    try:
        with factory() as session:
            user = build_user(role="agent")
            ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com",
                                  channel="web", subject="创建失败", description="测试", priority="medium", status="open")
            session.add_all([user, ticket])
            session.commit()
            yield SimpleNamespace(session=session, engine=engine, user=user, ticket=ticket,
                                  user_id=user.id, ticket_id=ticket.id)
    finally:
        engine.dispose()


def request(case, ticket_id=None):
    """构造请求，不携带自报身份字段。"""
    return ApprovalCreateRequest(ticket_id=ticket_id or case.ticket_id, request_reason="申请关闭")


def assert_failure(case, *, outcome, code, role="agent", target=None, ticket_status="open"):
    """独立连接观察持久化事件及业务不变，避免原会话身份映射干扰。"""
    with Session(case.engine) as check:
        saved, = list(check.scalars(select(AuditEventRecord)))
        assert saved.event_type == ("approval.denied" if outcome == "denied" else "approval.failed")
        assert (saved.actor_id, saved.actor_role, saved.outcome, saved.error_code) == (case.user_id, role, outcome, code)
        assert saved.ticket_id == (target or case.ticket_id)
        assert saved.approval_id is None and saved.operation_id is None
        assert saved.request_id is None and saved.trace_id is None
        assert list(check.scalars(select(ApprovalRecord))) == []
        assert check.get(TicketRecord, case.ticket_id).status == ticket_status


@pytest.mark.parametrize("role,active,expected_role", [("agent", False, "agent"), ("unknown", True, "unknown"), ("supervisor", False, "supervisor")])
def test_permission_failure_records_snapshot(case, role, active, expected_role):
    """直接Service调用的停用及未知角色尝试，保存可信身份快照。"""
    case.user.role, case.user.is_active = role, active
    case.session.commit()
    with pytest.raises(service.ApprovalAccessError, match="当前用户不能提交"):
        service.create_approval(session=case.session, request=request(case), requester=case.user)
    assert_failure(case, outcome="denied", code="permission_denied", role=expected_role)


def test_missing_target_records_attempted_identifier(case):
    """不存在的目标编号只代表尝试，不伪造申请或操作编号。"""
    target = str(uuid4())
    with pytest.raises(service.ApprovalTicketNotFoundError):
        service.create_approval(session=case.session, request=request(case, target), requester=case.user)
    assert_failure(case, outcome="failed", code="not_found", target=target)


def test_closed_target_records_conflict(case):
    """已关闭工单不创建申请，但独立保存冲突事件。"""
    case.ticket.status = "closed"
    case.session.commit()
    with pytest.raises(service.ApprovalConflictError):
        service.create_approval(session=case.session, request=request(case), requester=case.user)
    assert_failure(case, outcome="failed", code="state_conflict", ticket_status="closed")


@pytest.mark.parametrize("point", ["query", "after_flush", "commit"])
def test_storage_failure_rolls_back_then_records_on_same_database(case, monkeypatch, point):
    """查询、实际写入后、业务提交失败，均保留原异常链并独立提交事件。"""
    original_add = repository.add_approval
    original_error = SQLAlchemyError("测试业务数据库错误")

    def broken_query(*args, **kwargs):
        """模拟查询异常。"""
        raise original_error

    def broken_add(session, record):
        """真正flush后抛错，确保不存在半份成功申请。"""
        original_add(session, record)
        raise original_error

    def broken_commit():
        """只让业务Session提交失败，新审计Session仍能提交。"""
        raise original_error

    if point == "query":
        monkeypatch.setattr(repository, "get_ticket_for_approval", broken_query)
    elif point == "after_flush":
        monkeypatch.setattr(repository, "add_approval", broken_add)
    else:
        monkeypatch.setattr(case.session, "commit", broken_commit)
    with pytest.raises(service.ApprovalStorageError) as captured:
        service.create_approval(session=case.session, request=request(case), requester=case.user)
    assert captured.value.__cause__ is original_error
    assert_failure(case, outcome="failed", code="storage_error")


@pytest.mark.parametrize("kind", ["permission", "missing", "conflict", "storage"])
def test_audit_database_error_does_not_replace_business_error(case, monkeypatch, kind, caplog):
    """独立审计失败时，原权限/不存在/冲突/存储错误仍正确传播。"""
    target = case.ticket_id
    original = SQLAlchemyError("业务故障样本")
    expected = service.ApprovalStorageError
    if kind == "permission":
        case.user.is_active = False
        expected = service.ApprovalAccessError
    elif kind == "missing":
        target = str(uuid4())
        expected = service.ApprovalTicketNotFoundError
    elif kind == "conflict":
        case.ticket.status = "closed"
        expected = service.ApprovalConflictError
    case.session.commit()

    def broken_audit(*args, **kwargs):
        """审计数据库异常不能把原业务错误覆盖掉。"""
        raise SQLAlchemyError("private-audit-marker")

    def broken_query(*args, **kwargs):
        """提供可精确核对的原异常对象。"""
        raise original

    monkeypatch.setattr(audit_repository, "add_event", broken_audit)
    if kind == "storage":
        monkeypatch.setattr(repository, "get_ticket_for_approval", broken_query)
    with pytest.raises(expected) as captured:
        service.create_approval(session=case.session, request=request(case, target), requester=case.user)
    if kind == "storage":
        assert captured.value.__cause__ is original
    with Session(case.engine) as check:
        assert list(check.scalars(select(AuditEventRecord))) == []
        assert list(check.scalars(select(ApprovalRecord))) == []
    assert "失败审计未能保存" in caplog.text and "private-audit-marker" not in caplog.text


def test_rollback_failure_preserves_original_and_skips_new_write(case, monkeypatch, caplog):
    """原事务未清理时不尝试独立写入，回滚错误也不能覆盖原业务错误。"""
    original = SQLAlchemyError("原业务错误")

    def broken_query(*args, **kwargs):
        """模拟原业务故障。"""
        raise original

    def broken_rollback():
        """模拟原事务回滚异常。"""
        raise SQLAlchemyError("private-rollback-marker")

    def unexpected_attempt(*args, **kwargs):
        """回滚失败后不应开始新审计事务。"""
        raise AssertionError("不应尝试独立写入")

    monkeypatch.setattr(repository, "get_ticket_for_approval", broken_query)
    monkeypatch.setattr(audit_service, "record_failed_attempt", unexpected_attempt)
    # 在fixture清理之前恢复rollback，确保测试仍释放真实资源。
    with monkeypatch.context() as patch:
        patch.setattr(case.session, "rollback", broken_rollback)
        with pytest.raises(service.ApprovalStorageError) as captured:
            service.create_approval(session=case.session, request=request(case), requester=case.user)
    case.session.rollback()
    assert captured.value.__cause__ is original
    assert "事务回滚未能完成" in caplog.text and "private-rollback-marker" not in caplog.text


def test_success_does_not_invoke_independent_failure_writer(case, monkeypatch):
    """正常创建保留同事务成功审计，不调用失败保存接口。"""
    def unexpected_attempt(*args, **kwargs):
        """成功路径不能调用独立失败审计。"""
        raise AssertionError("成功路径不应独立保存审计")

    monkeypatch.setattr(audit_service, "record_failed_attempt", unexpected_attempt)
    record = service.create_approval(session=case.session, request=request(case), requester=case.user)
    with Session(case.engine) as check:
        saved, = list(check.scalars(select(AuditEventRecord)))
        assert (saved.event_type, saved.outcome, saved.approval_id) == ("approval.requested", "succeeded", record.id)


def test_failure_handler_does_not_reload_expired_identity(case):
    """禁止原Session在rollback后查询，仍能靠普通值快照保存失败事件。"""
    rolled_back = False

    def after_rollback(session):
        """记录原Session已回滚。"""
        nonlocal rolled_back
        rolled_back = True

    def forbid_reload(state):
        """后续ORM查询均视为错误，独立审计Session不受影响。"""
        if rolled_back:
            raise AssertionError("错误处理不应重读过期身份")

    event.listen(case.session, "after_rollback", after_rollback)
    event.listen(case.session, "do_orm_execute", forbid_reload)
    target = str(uuid4())
    with pytest.raises(service.ApprovalTicketNotFoundError):
        service.create_approval(session=case.session, request=request(case, target), requester=case.user)
    assert rolled_back
    assert_failure(case, outcome="failed", code="not_found", target=target)


def prepare_attempt(case, operation):
    """直接准备已提交前置申请，不为测试前置数据制造审计事件。"""
    case.user.role = "supervisor"
    record = ApprovalRecord(
        id=str(uuid4()), operation_id=str(uuid4()), ticket_id=case.ticket_id,
        requested_by=case.user_id, request_reason="申请关闭",
        status="pending" if operation == "review" else "approved",
        reviewed_by=case.user_id if operation == "execute" else None,
        reviewed_at=datetime.now(UTC) if operation == "execute" else None,
    )
    case.session.add(record)
    case.session.commit()
    return SimpleNamespace(id=record.id, operation_id=record.operation_id)


def invoke_attempt(case, operation, approval_id):
    """走正式业务函数，不能绕过权限检查或事务处理。"""
    if operation == "review":
        return service.review_approval(session=case.session, approval_id=approval_id, reviewer=case.user,
                                       request=ApprovalReviewRequest(approved=True, reason="已核验"))
    return execution.execute_approval(session=case.session, approval_id=approval_id, executor=case.user)


def assert_attempt_failure(case, approval_id, *, code, operation_id=None, outcome="failed", role="supervisor"):
    """从独立Session检查快照；无权限或未查到记录时不伪造目标信息。"""
    with Session(case.engine) as check:
        saved, = list(check.scalars(select(AuditEventRecord)))
        assert (saved.event_type, saved.outcome, saved.error_code) == (
            "approval.denied" if outcome == "denied" else "approval.failed", outcome, code)
        assert (saved.actor_id, saved.actor_role, saved.approval_id) == (case.user_id, role, approval_id)
        assert saved.operation_id == operation_id
        assert saved.ticket_id == (case.ticket_id if operation_id else None)


@pytest.mark.parametrize("operation", ["review", "execute"])
@pytest.mark.parametrize("role,active", [("agent", True), ("supervisor", False), ("unknown", True)])
def test_review_execution_denied_without_target_lookup(case, monkeypatch, operation, role, active):
    """拒绝角色/停用账户后不补查申请，目标只保留请求提供的申请编号。"""
    record = prepare_attempt(case, operation)
    case.user.role, case.user.is_active = role, active
    case.session.commit()

    def unexpected_lookup(*args, **kwargs):
        """权限失败不得为审计额外查询业务目标。"""
        raise AssertionError("权限失败不应查询申请")

    monkeypatch.setattr(repository, "get_approval_by_id", unexpected_lookup)
    with pytest.raises(service.ApprovalAccessError):
        invoke_attempt(case, operation, record.id)
    assert_attempt_failure(case, record.id, code="permission_denied", outcome="denied", role=role)


@pytest.mark.parametrize("operation", ["review", "execute"])
def test_review_execution_missing_target(case, operation):
    """不存在申请只记录尝试编号，没有工单和操作信息。"""
    prepare_attempt(case, operation)
    missing = str(uuid4())
    with pytest.raises(service.ApprovalNotFoundError):
        invoke_attempt(case, operation, missing)
    assert_attempt_failure(case, missing, code="not_found")


@pytest.mark.parametrize("operation", ["review", "execute"])
def test_review_execution_conflict_records_loaded_binding(case, operation):
    """已读取的目标快照在回滚后仍保留，业务状态不能被冲突覆盖。"""
    record = prepare_attempt(case, operation)
    case.session.get(ApprovalRecord, record.id).status = "rejected"
    case.session.commit()
    with pytest.raises(service.ApprovalConflictError):
        invoke_attempt(case, operation, record.id)
    assert_attempt_failure(case, record.id, code="state_conflict", operation_id=record.operation_id)
    with Session(case.engine) as check:
        assert check.get(ApprovalRecord, record.id).status == "rejected"
        assert check.get(TicketRecord, case.ticket_id).status == "open"


@pytest.mark.parametrize("operation", ["review", "execute"])
@pytest.mark.parametrize("point", ["query", "write", "after_write", "success_audit", "commit"])
def test_review_execution_storage_faults_keep_atomicity_and_failed_event(case, monkeypatch, operation, point):
    """各失败位置撤销本次效果及成功事件，独立保留一条失败事件与原异常链。"""
    record = prepare_attempt(case, operation)
    failure = SQLAlchemyError("业务数据库故障样本")
    write_name = "review_pending_approval" if operation == "review" else "claim_approved_request"
    after_name = "review_pending_approval" if operation == "review" else "add_execution"
    original_write = getattr(repository, after_name)
    original_audit = audit_repository.add_event

    def broken(*args, **kwargs):
        """模拟尚未完成的查询/写入/提交异常。"""
        raise failure

    def after_write(*args, **kwargs):
        """实际发送业务SQL后失败，检查真实回滚。"""
        original_write(*args, **kwargs)
        raise failure

    def after_success_audit(session, value):
        """真实写成功审计后失败，独立失败事件仍使用真实Repository。"""
        result = original_audit(session, value)
        if value.outcome == "succeeded":
            raise failure
        return result

    if point == "query":
        monkeypatch.setattr(repository, "get_approval_by_id", broken)
    elif point == "write":
        monkeypatch.setattr(repository, write_name, broken)
    elif point == "after_write":
        monkeypatch.setattr(repository, after_name, after_write)
    elif point == "success_audit":
        monkeypatch.setattr(audit_repository, "add_event", after_success_audit)
    else:
        monkeypatch.setattr(case.session, "commit", broken)
    with pytest.raises(service.ApprovalStorageError) as captured:
        invoke_attempt(case, operation, record.id)
    assert captured.value.__cause__ is failure
    assert_attempt_failure(case, record.id, code="storage_error",
                           operation_id=None if point == "query" else record.operation_id)
    with Session(case.engine) as check:
        assert check.get(ApprovalRecord, record.id).status == ("pending" if operation == "review" else "approved")
        assert check.get(TicketRecord, case.ticket_id).status == "open"
        assert list(check.scalars(select(ApprovalExecutionRecord))) == []


@pytest.mark.parametrize("operation", ["review", "execute"])
def test_review_execution_audit_failure_keeps_original_error(case, monkeypatch, operation):
    """独立审计不可用时不覆盖原业务数据库异常。"""
    record = prepare_attempt(case, operation)
    original = SQLAlchemyError("原业务数据库错误")

    def broken_query(*args, **kwargs):
        """保留原错误对象供异常链核对。"""
        raise original

    def broken_audit(*args, **kwargs):
        """独立审计数据库也不可用。"""
        raise SQLAlchemyError("审计数据库错误")

    monkeypatch.setattr(repository, "get_approval_by_id", broken_query)
    monkeypatch.setattr(audit_repository, "add_event", broken_audit)
    with pytest.raises(service.ApprovalStorageError) as captured:
        invoke_attempt(case, operation, record.id)
    assert captured.value.__cause__ is original
    with Session(case.engine) as check:
        assert list(check.scalars(select(AuditEventRecord))) == []


@pytest.mark.parametrize("operation", ["review", "execute"])
def test_review_execution_does_not_reload_after_rollback(case, operation):
    """禁止原Session回滚后再查询，目标和身份仍使用纯值快照。"""
    record = prepare_attempt(case, operation)
    case.session.get(ApprovalRecord, record.id).status = "rejected"
    case.session.commit()
    rolled_back = False

    def after_rollback(session):
        """跟踪原业务事务已经结束。"""
        nonlocal rolled_back
        rolled_back = True

    def forbid_reload(state):
        """独立审计使用新会话，不会进入此监听器。"""
        if rolled_back:
            raise AssertionError("不能回滚后重读ORM")

    event.listen(case.session, "after_rollback", after_rollback)
    event.listen(case.session, "do_orm_execute", forbid_reload)
    with pytest.raises(service.ApprovalConflictError):
        invoke_attempt(case, operation, record.id)
    assert_attempt_failure(case, record.id, code="state_conflict", operation_id=record.operation_id)


@pytest.mark.parametrize("operation", ["review", "execute"])
def test_review_execution_rollback_error_skips_audit(case, monkeypatch, operation):
    """回滚本身异常时不冒险开始新写入，也不覆盖原异常。"""
    record = prepare_attempt(case, operation)
    original = SQLAlchemyError("原业务故障")

    def broken_query(*args, **kwargs):
        """提供原业务异常。"""
        raise original

    def broken_rollback():
        """模拟原事务无法正常清理。"""
        raise SQLAlchemyError("回滚故障")

    def unexpected_attempt(*args, **kwargs):
        """回滚失败后不得尝试独立审计。"""
        raise AssertionError("不能开始新审计事务")

    monkeypatch.setattr(repository, "get_approval_by_id", broken_query)
    monkeypatch.setattr(audit_service, "record_failed_attempt", unexpected_attempt)
    with monkeypatch.context() as patch:
        patch.setattr(case.session, "rollback", broken_rollback)
        with pytest.raises(service.ApprovalStorageError) as captured:
            invoke_attempt(case, operation, record.id)
    case.session.rollback()
    assert captured.value.__cause__ is original


@pytest.mark.parametrize("point", ["replay_audit", "commit"])
def test_replay_failure_preserves_original_effect_and_receipt(case, monkeypatch, point):
    """重复请求失败不能撤销首个已提交成功，只撤销本次重放事件并记录失败。"""
    record = prepare_attempt(case, "execute")
    invoke_attempt(case, "execute", record.id)
    original_audit = audit_repository.add_event
    failure = SQLAlchemyError("重复请求故障")

    def broken_replay(session, value):
        """实际写入重放事件后失败，失败事件允许独立保存。"""
        result = original_audit(session, value)
        if value.event_type == "execution.replayed":
            raise failure
        return result

    def broken_commit():
        """仅让本次重放的业务会话提交失败。"""
        raise failure

    if point == "replay_audit":
        monkeypatch.setattr(audit_repository, "add_event", broken_replay)
    else:
        monkeypatch.setattr(case.session, "commit", broken_commit)
    with pytest.raises(service.ApprovalStorageError) as captured:
        invoke_attempt(case, "execute", record.id)
    assert captured.value.__cause__ is failure
    with Session(case.engine) as check:
        saved = list(check.scalars(select(AuditEventRecord)))
        assert {item.event_type for item in saved} == {"ticket.closed", "approval.failed"}
        assert len(saved) == 2 and all(item.operation_id == record.operation_id for item in saved)
        assert len(list(check.scalars(select(ApprovalExecutionRecord)))) == 1
        assert check.get(ApprovalRecord, record.id).status == "succeeded"
        assert check.get(TicketRecord, case.ticket_id).status == "closed"
