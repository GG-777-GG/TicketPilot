"""
文件职责：提供审批业务使用的数据库查询和写入操作。
作用与配合：由审批 Service 调用，共用同一次请求的 Session。
边界：不处理权限、不决定业务状态，也不提交或回滚事务；
数据库异常交给 Service 统一处理。
"""
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from datetime import datetime
from ticketpilot.approvals.models import (
    ApprovalExecutionRecord,
    ApprovalRecord,
)
from ticketpilot.tickets.models import TicketRecord


def get_ticket_for_approval(
    session: Session,
    ticket_id: str,
) -> TicketRecord | None:
    """按主键查询申请关联的工单；不存在时返回 None。"""

    return session.get(TicketRecord, ticket_id)


def add_approval(
    session: Session,
    record: ApprovalRecord,
) -> None:
    """写入审批对象，并在当前事务内检查数据库约束。"""

    session.add(record)

    # 发送 INSERT，但尚未提交事务。
    session.flush()

def get_approval_by_id(
    session: Session,
    approval_id: str,
    *,
    requested_by: str | None,
) -> ApprovalRecord | None:
    """按编号查询，并按调用方给定的申请人范围过滤。"""

    statement = select(ApprovalRecord).where(
        ApprovalRecord.id == approval_id
    )

    # 有申请人编号时，只查询这个人提交的记录。
    # None 表示调用方已确认允许查询全部申请人。
    if requested_by is not None:
        statement = statement.where(
            ApprovalRecord.requested_by == requested_by
        )

    return session.scalar(statement)


def review_pending_approval(
    session: Session,
    approval_id: str,
    *,
    reviewer_id: str,
    approved: bool,
    reason: str,
    reviewed_at: datetime,
) -> bool:
    """仅更新仍待审批的申请，返回本次是否成功写入决定。"""

    statement = (
        update(ApprovalRecord)
        .where(
            ApprovalRecord.id == approval_id,
            ApprovalRecord.status == "pending",
        )
        .values(
            status="approved" if approved else "rejected",
            reviewed_by=reviewer_id,
            approval_reason=reason,
            reviewed_at=reviewed_at,
            updated_at=reviewed_at,
        )
        # 由 Service 显式刷新 ORM 对象，读取最新字段。
        .execution_options(synchronize_session=False)
    )

    result = session.execute(statement)

    # 执行 UPDATE，但不在 Repository 中提交事务。
    return result.rowcount == 1


def claim_approved_request(
    session: Session,
    approval_id: str,
    now: datetime,
) -> bool:
    """仅允许已批准申请在当前事务中进入执行状态。"""

    statement = (
        update(ApprovalRecord)
        .where(
            ApprovalRecord.id == approval_id,
            ApprovalRecord.status == "approved",
        )
        .values(
            status="executing",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )

    return session.execute(statement).rowcount == 1


def close_ticket_for_approval(
    session: Session,
    ticket_id: str,
    now: datetime,
) -> bool:
    """仅关闭仍处于允许状态的工单，不提交事务。"""

    statement = (
        update(TicketRecord)
        .where(
            TicketRecord.id == ticket_id,
            TicketRecord.status.in_(
                ("open", "in_progress", "resolved")
            ),
        )
        .values(
            status="closed",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )

    return session.execute(statement).rowcount == 1


def get_execution(
    session: Session,
    operation_id: str,
) -> ApprovalExecutionRecord | None:
    """按操作编号读取已有成功结果。"""

    return session.get(
        ApprovalExecutionRecord,
        operation_id,
    )


def add_execution(
    session: Session,
    record: ApprovalExecutionRecord,
) -> None:
    """写入成功结果，事务仍由上层统一管理。"""

    session.add(record)
    session.flush()