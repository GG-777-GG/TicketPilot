"""
文件职责：验证审批 ORM 写入及数据库约束。
作用与配合：使用共享的外键启用 SQLite fixture 和正式 ApprovalRecord。
边界：仅操作内存测试数据库，不验证 HTTP 权限或实际工单执行。
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.approvals.models import ApprovalRecord
from ticketpilot.identity.models import UserRecord
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration


@pytest.fixture
def approval_record(database_session: Session) -> ApprovalRecord:
    """创建父记录并构造审批对象，保持审批对象尚未入库。"""

    requester = build_user(role="agent")
    reviewer = build_user(role="supervisor")
    ticket = TicketRecord(
        id=str(uuid4()), customer_name="测试客户", contact_email="customer@example.com",
        channel="web", subject="测试工单", description="申请关闭", priority="medium",
    )
    database_session.add_all([requester, reviewer, ticket])
    database_session.flush()
    return ApprovalRecord(
        id=str(uuid4()), operation_id=str(uuid4()), ticket_id=ticket.id,
        requested_by=requester.id, reviewed_by=reviewer.id, request_reason="问题已解决",
    )


def test_pending_approval_defaults_and_nullable_fields(database_session: Session, approval_record: ApprovalRecord) -> None:
    """新记录有默认操作、状态、时间，允许审批信息尚未填写。"""

    approval_record.reviewed_by = None
    database_session.add(approval_record)
    database_session.flush()
    database_session.refresh(approval_record)
    assert approval_record.status == "pending"
    assert approval_record.action == "close_ticket"
    assert approval_record.created_at is not None
    assert approval_record.updated_at is not None
    assert approval_record.reviewed_by is None
    assert approval_record.approval_reason is None
    assert approval_record.reviewed_at is None
    assert approval_record.result is None


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "executing", "succeeded", "failed"])
def test_declared_status_can_be_saved(database_session: Session, approval_record: ApprovalRecord, status: str) -> None:
    """仅验证合法状态值可保存；状态流转及字段一致性由后续服务处理。"""

    approval_record.status = status
    database_session.add(approval_record)
    database_session.flush()
    database_session.refresh(approval_record)
    assert approval_record.status == status


@pytest.mark.parametrize("field, value", [("status", "unknown"), ("action", "delete_ticket")])
def test_database_rejects_invalid_status_or_action(database_session: Session, approval_record: ApprovalRecord, field: str, value: str) -> None:
    """绕过 Pydantic 直接写 ORM，数据库仍拒绝非法值。"""

    setattr(approval_record, field, value)
    database_session.add(approval_record)
    with pytest.raises(IntegrityError):
        database_session.flush()
    database_session.rollback()


@pytest.mark.parametrize("field", ["ticket_id", "requested_by", "reviewed_by"])
def test_database_rejects_missing_reference(database_session: Session, approval_record: ApprovalRecord, field: str) -> None:
    """审批不能引用不存在的工单或用户。"""

    setattr(approval_record, field, str(uuid4()))
    database_session.add(approval_record)
    with pytest.raises(IntegrityError):
        database_session.flush()
    database_session.rollback()


def test_operation_id_is_unique(database_session: Session, approval_record: ApprovalRecord) -> None:
    """不同审批记录不能共用业务操作编号。"""

    database_session.add(approval_record)
    database_session.flush()
    database_session.add(ApprovalRecord(
        id=str(uuid4()), operation_id=approval_record.operation_id,
        ticket_id=approval_record.ticket_id, requested_by=approval_record.requested_by,
        request_reason="另一个申请",
    ))
    with pytest.raises(IntegrityError):
        database_session.flush()
    database_session.rollback()


@pytest.mark.parametrize("reference", ["ticket_id", "requested_by", "reviewed_by"])
def test_referenced_record_cannot_be_deleted(database_session: Session, approval_record: ApprovalRecord, reference: str) -> None:
    """RESTRICT 保留审批记录对应的工单和身份关联。"""

    database_session.add(approval_record)
    database_session.flush()
    model = TicketRecord if reference == "ticket_id" else UserRecord
    referenced_id = getattr(approval_record, reference)
    with pytest.raises(IntegrityError):
        database_session.execute(delete(model).where(model.id == referenced_id))
    database_session.rollback()
