"""
文件职责：验证正式审批Service产生成功事件和重放事件，并保持事务一致性。
作用与配合：使用真实审批/执行/审计Repository及临时SQLite。
边界：不修改业务库，不代表HTTP认证依赖或工作流外层拒绝均已审计。
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.approvals import execution, service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.approvals.schemas import ApprovalCreateRequest, ApprovalReviewRequest
from ticketpilot.audit import repository as audit_repository
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def context(database_session):
    """先提交业务前置数据，后续故障不得撤销这些已有数据。"""
    owner, manager = build_user(role="agent"), build_user(role="supervisor")
    ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com",
                          channel="web", subject="审计集成", description="已解决", priority="medium", status="open")
    database_session.add_all([owner, manager, ticket])
    database_session.commit()
    return SimpleNamespace(owner=owner, manager=manager, ticket=ticket)


def create(session, ctx):
    """通过正式Service创建，确保测试不绕过业务接线。"""
    return service.create_approval(session=session, requester=ctx.owner,
                                   request=ApprovalCreateRequest(ticket_id=ctx.ticket.id, request_reason="申请关闭"))


def review(session, ctx, record, approved=True):
    """通过正式Service保存一次审批决定。"""
    return service.review_approval(session=session, approval_id=record.id, reviewer=ctx.manager,
                                   request=ApprovalReviewRequest(approved=approved, reason="已核验"))


def events(session):
    """读取所有审计行，不依赖时间戳相同情况下的排序。"""
    return list(session.scalars(select(AuditEventRecord)))


@pytest.mark.parametrize("role", ["agent", "supervisor", "admin"])
def test_creation_persists_authenticated_actor_and_target(database_session, context, role):
    """三类合法申请人的身份快照、关联编号及成功结果均真实保存。"""
    ctx = context
    ctx.owner.role = role
    database_session.commit()
    record = create(database_session, ctx)
    with Session(database_session.get_bind()) as check:
        event, = events(check)
        assert (event.event_type, event.outcome) == ("approval.requested", "succeeded")
        assert (event.actor_id, event.actor_role) == (ctx.owner.id, role)
        assert (event.approval_id, event.ticket_id, event.operation_id) == (record.id, ctx.ticket.id, record.operation_id)
        assert event.request_id is None and event.trace_id is None and event.error_code is None
        assert check.get(ApprovalRecord, record.id).status == "pending"
        assert check.get(TicketRecord, ctx.ticket.id).status == "open"


@pytest.mark.parametrize("role", ["supervisor", "admin"])
@pytest.mark.parametrize("approved", [True, False])
def test_review_persists_decision_as_success(database_session, context, role, approved):
    """拒绝申请也是保存成功的决定，不能误记为权限拒绝。"""
    ctx = context
    ctx.manager.role = role
    database_session.commit()
    record = create(database_session, ctx)
    review(database_session, ctx, record, approved)
    expected = "approval.approved" if approved else "approval.rejected"
    with Session(database_session.get_bind()) as check:
        saved = events(check)
        assert len(saved) == 2
        event, = [item for item in saved if item.event_type == expected]
        assert (event.actor_id, event.actor_role, event.outcome) == (ctx.manager.id, role, "succeeded")
        assert (event.approval_id, event.ticket_id, event.operation_id) == (record.id, ctx.ticket.id, record.operation_id)
        assert check.get(ApprovalRecord, record.id).status == ("approved" if approved else "rejected")


def test_execution_and_replay_have_distinct_events_and_one_effect(database_session, context, monkeypatch):
    """实际关闭一次，工单重开后的重放只保存新事件，不重复业务效果。"""
    ctx = context
    record = create(database_session, ctx)
    review(database_session, ctx, record)
    execution.execute_approval(session=database_session, approval_id=record.id, executor=ctx.manager)
    # SQLite读回DateTime不保留时区；基准也从数据库读取，避免混比内存值。
    database_session.refresh(record)
    receipt = database_session.get(ApprovalExecutionRecord, record.operation_id)
    original = receipt.executed_by, receipt.created_at, receipt.result, record.updated_at
    other = build_user(role="admin")
    database_session.refresh(ctx.ticket)
    ctx.ticket.status = "open"
    database_session.add(other)
    database_session.commit()

    def unexpected_close(*args, **kwargs):
        """重放时不允许再次调用关闭仓储。"""
        raise AssertionError("重放不能再次关闭工单")

    monkeypatch.setattr(execution.repository, "close_ticket_for_approval", unexpected_close)
    for _ in range(2):
        execution.execute_approval(session=database_session, approval_id=record.id, executor=other)
    with Session(database_session.get_bind()) as check:
        saved = events(check)
        assert len(saved) == 5 and len({item.id for item in saved}) == 5
        closed, = [item for item in saved if item.event_type == "ticket.closed"]
        replays = [item for item in saved if item.event_type == "execution.replayed"]
        assert (closed.actor_id, closed.outcome) == (ctx.manager.id, "succeeded")
        assert len(replays) == 2
        assert all((item.actor_id, item.actor_role, item.outcome) == (other.id, "admin", "replayed") for item in replays)
        assert all((item.approval_id, item.ticket_id, item.operation_id) == (record.id, ctx.ticket.id, record.operation_id) for item in saved)
        receipts = list(check.scalars(select(ApprovalExecutionRecord)))
        assert len(receipts) == 1
        receipt = receipts[0]
        stored = check.get(ApprovalRecord, record.id)
        assert (receipt.executed_by, receipt.created_at, receipt.result, stored.updated_at) == original
        assert check.get(TicketRecord, ctx.ticket.id).status == "open"


@pytest.mark.parametrize("stage", ["create", "review", "execute", "replay"])
@pytest.mark.parametrize("fault", ["after_flush", "commit"])
def test_audit_and_business_roll_back_together(database_session, context, monkeypatch, stage, fault):
    """审计实际flush后或最终提交失败，各业务阶段都不能残留半份成功。"""
    ctx = context
    record = None
    if stage != "create":
        record = create(database_session, ctx)
    if stage in ("execute", "replay"):
        review(database_session, ctx, record)
    if stage == "replay":
        execution.execute_approval(session=database_session, approval_id=record.id, executor=ctx.manager)
    ticket_id = ctx.ticket.id
    approval_id = record.id if record else None
    operation_id = record.operation_id if record else None
    before = {item.id for item in events(database_session)}
    expected_status = {"review": "pending", "execute": "approved", "replay": "succeeded"}.get(stage)
    real_add = audit_repository.add_event

    def broken_add(session, event):
        """先真实发送审计与业务SQL，再模拟后续数据库错误。"""
        real_add(session, event)
        raise SQLAlchemyError("模拟审计flush后的故障")

    def broken_commit():
        """模拟事务尚未成功提交的数据库错误。"""
        raise SQLAlchemyError("模拟提交失败")

    if fault == "after_flush":
        monkeypatch.setattr(audit_repository, "add_event", broken_add)
    else:
        monkeypatch.setattr(database_session, "commit", broken_commit)
    with pytest.raises(service.ApprovalStorageError):
        if stage == "create":
            create(database_session, ctx)
        elif stage == "review":
            review(database_session, ctx, record)
        else:
            execution.execute_approval(session=database_session, approval_id=approval_id, executor=ctx.manager)
    with Session(database_session.get_bind()) as check:
        saved = events(check)
        # 仅业务Session的commit故障不影响独立审计Session提交。
        if fault == "commit":
            assert before.issubset({item.id for item in saved})
            failure, = [item for item in saved if item.id not in before]
            assert (failure.event_type, failure.outcome, failure.error_code) == ("approval.failed", "failed", "storage_error")
            assert failure.approval_id == approval_id
            assert failure.operation_id == operation_id
        else:
            assert {item.id for item in saved} == before
        assert check.get(TicketRecord, ticket_id).status == ("closed" if stage == "replay" else "open")
        if stage == "create":
            assert list(check.scalars(select(ApprovalRecord))) == []
        else:
            assert check.get(ApprovalRecord, approval_id).status == expected_status
            assert (check.get(ApprovalExecutionRecord, operation_id) is not None) == (stage == "replay")


def test_disallowed_and_conflicting_attempts_do_not_add_success(database_session, context):
    """权限失败和重复决定记录失败尝试，不能误写成功事件。"""
    ctx = context
    record = create(database_session, ctx)
    with pytest.raises(service.ApprovalAccessError):
        service.review_approval(session=database_session, approval_id=record.id, reviewer=ctx.owner,
                                request=ApprovalReviewRequest(approved=True, reason="越权尝试"))
    with pytest.raises(service.ApprovalAccessError):
        execution.execute_approval(session=database_session, approval_id=record.id, executor=ctx.owner)
    assert len(events(database_session)) == 3
    assert sum(item.outcome == "denied" for item in events(database_session)) == 2
    review(database_session, ctx, record, approved=False)
    with pytest.raises(service.ApprovalConflictError):
        review(database_session, ctx, record)
    with pytest.raises(service.ApprovalConflictError):
        execution.execute_approval(session=database_session, approval_id=record.id, executor=ctx.manager)
    saved = events(database_session)
    assert {item.event_type for item in saved if item.outcome == "succeeded"} == {"approval.requested", "approval.rejected"}
    assert sum(item.outcome == "failed" for item in saved) == 2
    assert len(saved) == 6
