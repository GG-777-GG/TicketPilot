"""
文件职责：验证创建审批申请的业务规则和事务边界。
作用与配合：直接调用正式 Service/Repository，使用共享临时 SQLite。
边界：不验证 JWT 或 HTTP，不创建真实申请或关闭真实工单。
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.approvals import repository, service
from ticketpilot.approvals.models import ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalCreateRequest, ApprovalReviewRequest
from ticketpilot.identity.models import UserRecord
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def creation_context(database_session: Session) -> tuple[UserRecord, TicketRecord]:
    """先提交用户与工单，便于验证后续失败只回滚创建申请事务。"""

    user = build_user(role="agent")
    ticket = TicketRecord(
        id=str(uuid4()), customer_name="测试客户", contact_email="customer@example.com",
        channel="web", subject="申请关闭", description="问题已解决", priority="medium", status="open",
    )
    database_session.add_all([user, ticket])
    database_session.commit()
    return user, ticket


def approval_count(session: Session) -> int:
    """查询当前事务能看到的审批数量，兼顾验证错误后会话可继续使用。"""
    return session.scalar(select(func.count()).select_from(ApprovalRecord))


@pytest.mark.parametrize("role", ["agent", "supervisor", "admin"])
def test_allowed_user_creates_pending_approval(database_session: Session, creation_context, role: str) -> None:
    """允许的角色提交申请后，只新增待审批记录，工单保持原状态。"""

    user, ticket = creation_context
    user.role = role
    database_session.commit()
    record = service.create_approval(
        session=database_session,
        request=ApprovalCreateRequest(ticket_id=ticket.id, request_reason="  已解决  "),
        requester=user,
    )
    record_id = record.id
    database_session.expunge_all()
    stored = database_session.get(ApprovalRecord, record_id)
    assert stored is not None
    assert UUID(stored.id) != UUID(stored.operation_id)
    assert stored.requested_by == user.id
    assert stored.status == "pending" and stored.action == "close_ticket"
    assert stored.request_reason == "已解决"
    assert stored.reviewed_by is None and stored.reviewed_at is None
    assert stored.approval_reason is None and stored.result is None
    assert stored.created_at is not None and stored.updated_at is not None
    assert database_session.get(TicketRecord, ticket.id).status == "open"


@pytest.mark.parametrize("status", ["in_progress", "resolved"])
def test_non_closed_ticket_can_be_submitted(database_session: Session, creation_context, status: str) -> None:
    """当前规则允许为处理中或已解决的工单申请关闭。"""
    user, ticket = creation_context
    ticket.status = status
    database_session.commit()
    service.create_approval(session=database_session, request=ApprovalCreateRequest(ticket_id=ticket.id, request_reason="申请关闭"), requester=user)
    assert approval_count(database_session) == 1
    assert ticket.status == status


@pytest.mark.parametrize("role, active", [("agent", False), ("unknown", True)])
def test_disallowed_user_cannot_create(database_session: Session, creation_context, role: str, active: bool) -> None:
    """停用账号和未知角色不能提交，即使直接调用服务也要拒绝。"""
    user, ticket = creation_context
    user.role, user.is_active = role, active
    database_session.commit()
    with pytest.raises(service.ApprovalAccessError):
        service.create_approval(session=database_session, request=ApprovalCreateRequest(ticket_id=ticket.id, request_reason="申请关闭"), requester=user)
    assert approval_count(database_session) == 0


def test_missing_ticket_does_not_create_approval(database_session: Session, creation_context) -> None:
    """不存在的目标返回业务错误，不留下审批记录。"""
    user, _ = creation_context
    with pytest.raises(service.ApprovalTicketNotFoundError):
        service.create_approval(session=database_session, request=ApprovalCreateRequest(ticket_id=str(uuid4()), request_reason="申请关闭"), requester=user)
    assert approval_count(database_session) == 0


def test_closed_ticket_does_not_create_approval(database_session: Session, creation_context) -> None:
    """已关闭工单无需再次申请。"""
    user, ticket = creation_context
    ticket.status = "closed"
    database_session.commit()
    with pytest.raises(service.ApprovalConflictError):
        service.create_approval(session=database_session, request=ApprovalCreateRequest(ticket_id=ticket.id, request_reason="申请关闭"), requester=user)
    assert approval_count(database_session) == 0
    assert database_session.get(TicketRecord, ticket.id).status == "closed"


@pytest.mark.parametrize("failure_point", ["query", "after_flush", "commit"])
def test_database_failure_rolls_back_and_preserves_cause(database_session: Session, creation_context, monkeypatch: pytest.MonkeyPatch, failure_point: str) -> None:
    """查询、写入或提交错误统一转换；已 flush 的 INSERT 也必须撤销。"""
    user, ticket = creation_context
    original_add = repository.add_approval
    failure = SQLAlchemyError("测试故障")

    def fail_query(*_args):
        """模拟数据库查询失败。"""
        raise failure

    def fail_after_flush(session, record):
        """先真正 INSERT，再失败，检查事务没有提前提交。"""
        original_add(session, record)
        raise failure

    def fail_commit():
        """模拟事务提交失败。"""
        raise failure

    with monkeypatch.context() as patch:
        if failure_point == "query":
            patch.setattr(repository, "get_ticket_for_approval", fail_query)
        elif failure_point == "after_flush":
            patch.setattr(repository, "add_approval", fail_after_flush)
        else:
            patch.setattr(database_session, "commit", fail_commit)
        with pytest.raises(service.ApprovalStorageError) as caught:
            service.create_approval(session=database_session, request=ApprovalCreateRequest(ticket_id=ticket.id, request_reason="申请关闭"), requester=user)

    assert caught.value.__cause__ is failure
    assert approval_count(database_session) == 0
    assert database_session.get(TicketRecord, ticket.id).status == "open"


def test_repository_flush_does_not_commit(database_session: Session, creation_context) -> None:
    """Repository 插入的记录可由上层回滚，不能提前提交。"""
    user, ticket = creation_context
    repository.add_approval(database_session, ApprovalRecord(
        id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ticket.id,
        requested_by=user.id, request_reason="申请关闭",
    ))
    assert approval_count(database_session) == 1
    database_session.rollback()
    assert approval_count(database_session) == 0


@pytest.fixture
def query_record(database_session: Session, creation_context) -> ApprovalRecord:
    """预先加载一条别人的申请，查询仍必须执行所有权过滤。"""
    _viewer, ticket = creation_context
    owner = build_user(role="agent")
    database_session.add(owner)
    database_session.flush()
    record = ApprovalRecord(id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ticket.id, requested_by=owner.id, request_reason="申请关闭")
    database_session.add(record)
    database_session.commit()
    return record


@pytest.mark.parametrize("role, own, allowed", [
    ("agent", True, True), ("agent", False, False),
    ("supervisor", False, True), ("admin", False, True),
])
def test_query_scope_is_selected_from_viewer_role(database_session: Session, creation_context, query_record: ApprovalRecord, role: str, own: bool, allowed: bool, monkeypatch) -> None:
    """角色决定数据库过滤范围；对象已在 Session 中也不能绕过权限。"""
    viewer, _ = creation_context
    viewer.role = role
    if own:
        query_record.requested_by = viewer.id
    database_session.commit()

    def unexpected_commit():
        """只读服务不应提交事务。"""
        raise AssertionError("查询不应调用 commit")

    monkeypatch.setattr(database_session, "commit", unexpected_commit)
    if allowed:
        result = service.get_approval(session=database_session, approval_id=query_record.id, viewer=viewer)
        assert result.id == query_record.id
        assert result.status == "pending"
    else:
        with pytest.raises(service.ApprovalNotFoundError, match="审批申请不存在"):
            service.get_approval(session=database_session, approval_id=query_record.id, viewer=viewer)


@pytest.mark.parametrize("role, active", [("agent", False), ("unknown", True)])
def test_query_rejects_disallowed_viewer(database_session: Session, creation_context, query_record: ApprovalRecord, role: str, active: bool) -> None:
    """直接调用服务时，停用和未知角色也不能读取。"""
    viewer, _ = creation_context
    viewer.role, viewer.is_active = role, active
    database_session.commit()
    with pytest.raises(service.ApprovalAccessError):
        service.get_approval(session=database_session, approval_id=query_record.id, viewer=viewer)


def test_query_missing_record_returns_same_not_found(database_session: Session, creation_context) -> None:
    """不存在记录与越权查询对外使用相同业务错误。"""
    viewer, _ = creation_context
    with pytest.raises(service.ApprovalNotFoundError, match="审批申请不存在"):
        service.get_approval(session=database_session, approval_id=str(uuid4()), viewer=viewer)


def test_query_storage_error_is_wrapped_and_session_remains_usable(database_session: Session, creation_context, query_record: ApprovalRecord, monkeypatch) -> None:
    """查询故障回滚并转换存储异常，之后仍能使用 Session。"""
    viewer, _ = creation_context
    failure = SQLAlchemyError("查询故障")

    def fail(*_args, **_kwargs):
        """模拟读取时数据库故障。"""
        raise failure

    monkeypatch.setattr(repository, "get_approval_by_id", fail)
    with pytest.raises(service.ApprovalStorageError) as caught:
        service.get_approval(session=database_session, approval_id=query_record.id, viewer=viewer)
    assert caught.value.__cause__ is failure
    assert approval_count(database_session) == 1


@pytest.mark.parametrize("role", ["supervisor", "admin"])
@pytest.mark.parametrize("approved", [True, False])
def test_review_records_decision_without_executing_ticket(database_session: Session, creation_context, query_record: ApprovalRecord, role: str, approved: bool) -> None:
    """两种管理角色均可批准/拒绝，保存真实审批人但不关闭工单。"""
    reviewer, ticket = creation_context
    reviewer.role = role
    database_session.commit()
    operation_id = query_record.operation_id
    result = service.review_approval(session=database_session, approval_id=query_record.id, request=ApprovalReviewRequest(approved=approved, reason="  已核验  "), reviewer=reviewer)
    database_session.refresh(result)
    assert result.status == ("approved" if approved else "rejected")
    assert result.reviewed_by == reviewer.id and result.approval_reason == "已核验"
    assert result.reviewed_at is not None
    assert result.operation_id == operation_id and result.result is None
    assert database_session.get(TicketRecord, ticket.id).status == "open"


@pytest.mark.parametrize("role, active", [("agent", True), ("unknown", True), ("supervisor", False)])
def test_review_rejects_unprivileged_or_disabled_user(database_session: Session, creation_context, query_record: ApprovalRecord, role: str, active: bool) -> None:
    """直接调用服务同样执行角色与账号状态检查。"""
    reviewer, _ = creation_context
    reviewer.role, reviewer.is_active = role, active
    database_session.commit()
    with pytest.raises(service.ApprovalAccessError):
        service.review_approval(session=database_session, approval_id=query_record.id, request=ApprovalReviewRequest(approved=True, reason="理由"), reviewer=reviewer)
    database_session.refresh(query_record)
    assert query_record.status == "pending" and query_record.reviewed_by is None


@pytest.mark.parametrize("status", ["approved", "rejected", "executing", "succeeded", "failed"])
def test_review_cannot_overwrite_non_pending_record(database_session: Session, creation_context, query_record: ApprovalRecord, status: str) -> None:
    """任何非pending状态都不允许重写决定或重置操作编号。"""
    reviewer, _ = creation_context
    reviewer.role = "supervisor"
    query_record.status = status
    query_record.approval_reason = "原理由"
    database_session.commit()
    old_operation_id = query_record.operation_id
    with pytest.raises(service.ApprovalConflictError):
        service.review_approval(session=database_session, approval_id=query_record.id, request=ApprovalReviewRequest(approved=True, reason="覆盖理由"), reviewer=reviewer)
    database_session.refresh(query_record)
    assert query_record.status == status and query_record.approval_reason == "原理由"
    assert query_record.operation_id == old_operation_id


def test_review_missing_record_returns_not_found(database_session: Session, creation_context) -> None:
    """管理角色审批不存在申请时得到业务404类型。"""
    reviewer, _ = creation_context
    reviewer.role = "admin"
    database_session.commit()
    with pytest.raises(service.ApprovalNotFoundError):
        service.review_approval(session=database_session, approval_id=str(uuid4()), request=ApprovalReviewRequest(approved=False, reason="理由"), reviewer=reviewer)


@pytest.mark.parametrize("failure_point", ["query", "after_update", "refresh", "commit"])
def test_review_storage_failure_rolls_back_decision(database_session: Session, creation_context, query_record: ApprovalRecord, monkeypatch, failure_point: str) -> None:
    """更新已经执行后，刷新/提交失败也必须撤销审批字段。"""
    reviewer, _ = creation_context
    reviewer.role = "supervisor"
    database_session.commit()
    original_update = repository.review_pending_approval
    failure = SQLAlchemyError("审批数据库故障")

    def fail(*_args, **_kwargs):
        """模拟任意数据库阶段失败。"""
        raise failure

    def fail_after_update(*args, **kwargs):
        """先执行条件UPDATE，再抛出数据库异常。"""
        original_update(*args, **kwargs)
        raise failure

    with monkeypatch.context() as patch:
        if failure_point == "query":
            patch.setattr(repository, "get_approval_by_id", fail)
        elif failure_point == "after_update":
            patch.setattr(repository, "review_pending_approval", fail_after_update)
        else:
            patch.setattr(database_session, failure_point, fail)
        with pytest.raises(service.ApprovalStorageError) as caught:
            service.review_approval(session=database_session, approval_id=query_record.id, request=ApprovalReviewRequest(approved=True, reason="理由"), reviewer=reviewer)
    assert caught.value.__cause__ is failure
    database_session.refresh(query_record)
    assert query_record.status == "pending"
    assert query_record.reviewed_by is None and query_record.reviewed_at is None
    assert query_record.approval_reason is None


def test_review_repository_does_not_commit(database_session: Session, creation_context, query_record: ApprovalRecord) -> None:
    """条件UPDATE仍属于上层事务，可以整体回滚。"""
    reviewer, _ = creation_context
    assert repository.review_pending_approval(database_session, query_record.id, reviewer_id=reviewer.id, approved=True, reason="理由", reviewed_at=datetime.now(UTC))
    database_session.rollback()
    database_session.refresh(query_record)
    assert query_record.status == "pending" and query_record.reviewed_by is None
