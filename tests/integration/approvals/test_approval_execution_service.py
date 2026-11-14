"""
文件职责：验证真正关闭工单的权限、事务、幂等返回与故障恢复。
作用与配合：直接调用执行服务与真实Repository，使用临时SQLite。
边界：不调用HTTP，不修改业务库，不验证PostgreSQL隔离行为。
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.approvals import execution, repository, service
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def execution_context(database_session: Session):
    """先提交具有完整审批信息的申请，隔离执行服务的测试范围。"""
    owner, executor = build_user(role="agent"), build_user(role="supervisor")
    ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com", channel="web", subject="执行服务", description="问题已解决", priority="medium", status="open")
    database_session.add_all([owner, executor, ticket])
    database_session.flush()
    record = ApprovalRecord(id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ticket.id, requested_by=owner.id, request_reason="申请关闭", status="approved", reviewed_by=executor.id, approval_reason="已核验", reviewed_at=datetime.now(UTC))
    database_session.add(record)
    database_session.commit()
    return SimpleNamespace(owner=owner, executor=executor, ticket=ticket, record=record)


def result_count(session: Session) -> int:
    """查询成功结果总数，同时验证错误后的会话仍可使用。"""
    return session.scalar(select(func.count()).select_from(ApprovalExecutionRecord))


@pytest.mark.parametrize("role", ["supervisor", "admin"])
@pytest.mark.parametrize("ticket_status", ["open", "in_progress", "resolved"])
def test_execution_commits_ticket_record_and_receipt(database_session: Session, execution_context, role: str, ticket_status: str) -> None:
    """三处变更持久保存，结果使用申请里的原操作编号。"""
    ctx = execution_context
    ctx.executor.role, ctx.ticket.status = role, ticket_status
    database_session.commit()
    approval_id, operation_id, ticket_id, executor_id = ctx.record.id, ctx.record.operation_id, ctx.ticket.id, ctx.executor.id
    result = execution.execute_approval(session=database_session, approval_id=approval_id, executor=ctx.executor)
    assert result.status == "succeeded"
    database_session.expunge_all()
    saved = database_session.get(ApprovalRecord, approval_id)
    receipt = database_session.get(ApprovalExecutionRecord, operation_id)
    assert database_session.get(TicketRecord, ticket_id).status == "closed"
    assert saved.status == "succeeded" and saved.operation_id == operation_id
    assert saved.result == receipt.result == f"工单 {ticket_id} 已关闭。"
    assert receipt.ticket_id == ticket_id and receipt.executed_by == executor_id
    assert receipt.action == "close_ticket" and receipt.created_at is not None
    assert result_count(database_session) == 1


@pytest.mark.parametrize("role, active", [("agent", True), ("unknown", True), ("supervisor", False)])
def test_execution_rejects_disallowed_executor(database_session: Session, execution_context, role: str, active: bool) -> None:
    """角色和账号状态在执行前重新检查。"""
    ctx = execution_context
    ctx.executor.role, ctx.executor.is_active = role, active
    database_session.commit()
    with pytest.raises(service.ApprovalAccessError):
        execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    database_session.refresh(ctx.record)
    assert ctx.record.status == "approved" and result_count(database_session) == 0


@pytest.mark.parametrize("status", ["pending", "rejected", "executing", "failed", "succeeded"])
def test_execution_rejects_state_without_approved_or_successful_receipt(database_session: Session, execution_context, status: str) -> None:
    """只有approved可首次执行；孤立succeeded也不能作为成功凭据。"""
    ctx = execution_context
    ctx.record.status = status
    database_session.commit()
    with pytest.raises(service.ApprovalConflictError):
        execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    database_session.refresh(ctx.record)
    database_session.refresh(ctx.ticket)
    assert ctx.record.status == status and ctx.ticket.status == "open"
    assert result_count(database_session) == 0


def test_execution_missing_request_returns_not_found(database_session: Session, execution_context) -> None:
    """管理角色执行不存在的申请时返回业务错误。"""
    with pytest.raises(service.ApprovalNotFoundError):
        execution.execute_approval(session=database_session, approval_id=str(uuid4()), executor=execution_context.executor)
    assert result_count(database_session) == 0


@pytest.mark.parametrize("field", ["reviewed_by", "reviewed_at"])
def test_incomplete_approval_rolls_back_claim(database_session: Session, execution_context, field: str) -> None:
    """审批信息缺失时不能执行，也不能残留executing状态。"""
    ctx = execution_context
    setattr(ctx.record, field, None)
    database_session.commit()
    with pytest.raises(service.ApprovalConflictError):
        execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    database_session.refresh(ctx.record)
    assert ctx.record.status == "approved" and result_count(database_session) == 0


@pytest.mark.parametrize("status", ["closed", "unknown"])
def test_ticket_state_conflict_rolls_back_claim(database_session: Session, execution_context, status: str) -> None:
    """工单已关闭或处于未知状态时不能强制覆盖。"""
    ctx = execution_context
    ctx.ticket.status = status
    database_session.commit()
    with pytest.raises(service.ApprovalConflictError):
        execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    database_session.refresh(ctx.record)
    database_session.refresh(ctx.ticket)
    assert ctx.record.status == "approved" and ctx.ticket.status == status
    assert result_count(database_session) == 0


def test_successful_replay_returns_original_result_without_closing_again(database_session: Session, execution_context, monkeypatch) -> None:
    """重复请求保留首个执行人/时间/结果，即使工单后来被其他流程重开。"""
    ctx = execution_context
    first = execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    database_session.refresh(first)
    receipt = database_session.get(ApprovalExecutionRecord, first.operation_id)
    original = receipt.executed_by, receipt.created_at, receipt.result, first.updated_at
    another_executor = build_user(role="admin")
    database_session.refresh(ctx.ticket)
    ctx.ticket.status = "open"
    database_session.add(another_executor)
    database_session.commit()

    def unexpected_close(*_args, **_kwargs):
        """重放成功结果不应再进入工单修改步骤。"""
        raise AssertionError("不应重复关闭工单")

    monkeypatch.setattr(repository, "close_ticket_for_approval", unexpected_close)
    second = execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=another_executor)
    database_session.refresh(receipt)
    database_session.refresh(ctx.ticket)
    assert (receipt.executed_by, receipt.created_at, receipt.result, second.updated_at) == original
    assert second.result == first.result and ctx.ticket.status == "open"
    assert result_count(database_session) == 1


@pytest.mark.parametrize("corruption", ["result", "ticket_id", "approved_with_receipt"])
def test_inconsistent_receipt_is_not_reused(database_session: Session, execution_context, corruption: str) -> None:
    """结果与申请目标、文本或状态不一致时拒绝复用。"""
    ctx = execution_context
    execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    receipt = database_session.get(ApprovalExecutionRecord, ctx.record.operation_id)
    if corruption == "result":
        receipt.result = "不一致的文本"
    elif corruption == "ticket_id":
        other = TicketRecord(id=str(uuid4()), customer_name="其他", contact_email="other@example.com", channel="web", subject="其他工单", description="测试", priority="medium")
        database_session.add(other)
        database_session.flush()
        receipt.ticket_id = other.id
    else:
        ctx.record.status = "approved"
    database_session.commit()
    with pytest.raises(service.ApprovalConflictError):
        execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    assert result_count(database_session) == 1


@pytest.mark.parametrize("failure_point", ["query", "after_claim", "refresh", "get_receipt", "after_close", "after_receipt", "commit"])
def test_execution_failure_rolls_back_all_changes_and_can_retry(database_session: Session, execution_context, monkeypatch, failure_point: str) -> None:
    """关键数据库阶段失败后，恢复到approved/open/无成功结果并可重试。"""
    ctx = execution_context
    original_claim, original_close, original_add = repository.claim_approved_request, repository.close_ticket_for_approval, repository.add_execution
    failure = SQLAlchemyError("执行事务故障")

    def fail(*_args, **_kwargs):
        """模拟数据库故障。"""
        raise failure

    def fail_after_claim(*args, **kwargs):
        """申请状态UPDATE之后失败。"""
        original_claim(*args, **kwargs)
        raise failure

    def fail_after_close(*args, **kwargs):
        """工单关闭UPDATE之后失败。"""
        original_close(*args, **kwargs)
        raise failure

    def fail_after_receipt(*args, **kwargs):
        """成功记录及申请结果已flush，尚未commit时失败。"""
        original_add(*args, **kwargs)
        raise failure

    with monkeypatch.context() as patch:
        targets = {
            "query": (repository, "get_approval_by_id", fail),
            "after_claim": (repository, "claim_approved_request", fail_after_claim),
            "refresh": (database_session, "refresh", fail),
            "get_receipt": (repository, "get_execution", fail),
            "after_close": (repository, "close_ticket_for_approval", fail_after_close),
            "after_receipt": (repository, "add_execution", fail_after_receipt),
            "commit": (database_session, "commit", fail),
        }
        patch.setattr(*targets[failure_point])
        with pytest.raises(service.ApprovalStorageError) as caught:
            execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    assert caught.value.__cause__ is failure
    database_session.refresh(ctx.record)
    database_session.refresh(ctx.ticket)
    assert ctx.record.status == "approved" and ctx.record.result is None
    assert ctx.ticket.status == "open" and result_count(database_session) == 0
    result = execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    assert result.status == "succeeded" and result_count(database_session) == 1


def test_retry_after_commit_succeeded_but_caller_received_error(database_session: Session, execution_context, monkeypatch) -> None:
    """模拟提交已生效但调用方收到错误，重试仍复用已提交结果。"""
    ctx = execution_context
    original_commit = database_session.commit

    def commit_then_fail():
        """先真正提交，再模拟调用方收到异常。"""
        original_commit()
        raise SQLAlchemyError("提交后响应失败")

    with monkeypatch.context() as patch:
        patch.setattr(database_session, "commit", commit_then_fail)
        with pytest.raises(service.ApprovalStorageError):
            execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    result = execution.execute_approval(session=database_session, approval_id=ctx.record.id, executor=ctx.executor)
    assert result.status == "succeeded" and result_count(database_session) == 1
