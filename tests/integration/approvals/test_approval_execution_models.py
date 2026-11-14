"""
文件职责：验证正式执行结果的主键、关联、字段约束及事务回滚。
作用与配合：使用共享外键启用的临时SQLite和正式ORM。
边界：不代替执行服务权限、幂等流程及PostgreSQL行为测试。
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.approvals.models import ApprovalExecutionRecord, ApprovalRecord
from ticketpilot.identity.models import UserRecord
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def execution_context(database_session: Session):
    """先提交不同申请人/执行人与目标工单，返回尚未保存的成功记录。"""
    owner, executor = build_user(role="agent"), build_user(role="admin")
    ticket = TicketRecord(id=str(uuid4()), customer_name="测试", contact_email="test@example.com", channel="web", subject="执行测试", description="测试", priority="medium", status="open")
    database_session.add_all([owner, executor, ticket])
    database_session.flush()
    approval = ApprovalRecord(id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ticket.id, requested_by=owner.id, request_reason="申请关闭", status="approved")
    database_session.add(approval)
    database_session.commit()
    execution = ApprovalExecutionRecord(operation_id=approval.operation_id, ticket_id=ticket.id, executed_by=executor.id, result="工单已关闭")
    return approval, execution


def test_execution_defaults_and_persisted_result(database_session: Session, execution_context) -> None:
    """结果按操作编号读回，数据库填充默认操作与创建时间。"""
    approval, execution = execution_context
    database_session.add(execution)
    database_session.flush()
    database_session.refresh(execution)
    assert execution.operation_id == approval.operation_id
    assert execution.action == "close_ticket"
    assert execution.created_at is not None
    operation_id = execution.operation_id
    database_session.expunge(execution)
    stored = database_session.get(ApprovalExecutionRecord, operation_id)
    assert stored.result == "工单已关闭"


def test_execution_operation_id_is_unique(database_session: Session, execution_context) -> None:
    """同一操作不能产生第二条成功记录。"""
    _approval, execution = execution_context
    values = dict(operation_id=execution.operation_id, ticket_id=execution.ticket_id, executed_by=execution.executed_by, result="重复结果")
    database_session.add(execution)
    database_session.flush()
    database_session.expunge(execution)
    database_session.add(ApprovalExecutionRecord(**values))
    with pytest.raises(IntegrityError):
        database_session.flush()
    database_session.rollback()


@pytest.mark.parametrize("reference", ["unknown_operation", "approval_id", "ticket_id", "executed_by"])
def test_execution_rejects_invalid_reference(database_session: Session, execution_context, reference: str) -> None:
    """操作编号必须引用申请的operation_id，不能误用审批主键id。"""
    approval, execution = execution_context
    if reference == "approval_id":
        execution.operation_id = approval.id
    elif reference == "unknown_operation":
        execution.operation_id = str(uuid4())
    else:
        setattr(execution, reference, str(uuid4()))
    database_session.add(execution)
    with pytest.raises(IntegrityError):
        database_session.flush()
    database_session.rollback()


@pytest.mark.parametrize("field, value", [("action", "delete_ticket"), ("result", None)])
def test_execution_rejects_invalid_action_or_missing_result(database_session: Session, execution_context, field: str, value) -> None:
    """只允许固定操作，成功结果必须实际保存。"""
    _approval, execution = execution_context
    setattr(execution, field, value)
    database_session.add(execution)
    with pytest.raises(IntegrityError):
        database_session.flush()
    database_session.rollback()


@pytest.mark.parametrize("reference", ["approval", "executor"])
def test_execution_restricts_deleting_referenced_records(database_session: Session, execution_context, reference: str) -> None:
    """成功记录存在时不能删除其申请或独立的执行人。"""
    approval, execution = execution_context
    database_session.add(execution)
    database_session.flush()
    model = ApprovalRecord if reference == "approval" else UserRecord
    record_id = approval.id if reference == "approval" else execution.executed_by
    with pytest.raises(IntegrityError):
        database_session.execute(delete(model).where(model.id == record_id))
    database_session.rollback()


def test_ticket_and_execution_record_roll_back_together(database_session: Session, execution_context) -> None:
    """同一事务中的工单变化、申请状态和成功凭据可以一起撤销。"""
    approval, execution = execution_context
    operation_id, ticket_id, approval_id = execution.operation_id, execution.ticket_id, approval.id
    ticket = database_session.get(TicketRecord, ticket_id)
    ticket.status = "closed"
    approval.status, approval.result = "succeeded", "工单已关闭"
    database_session.add(execution)
    database_session.flush()
    database_session.rollback()
    assert database_session.get(TicketRecord, ticket_id).status == "open"
    assert database_session.get(ApprovalRecord, approval_id).status == "approved"
    assert database_session.get(ApprovalRecord, approval_id).result is None
    assert database_session.get(ApprovalExecutionRecord, operation_id) is None
