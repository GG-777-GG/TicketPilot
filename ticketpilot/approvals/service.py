"""
文件职责：实现创建工单审批申请的业务规则。
作用与配合：后续由审批 Router 提供已认证用户、请求和 Session，
本模块调用 Repository，并统一管理本次事务。
边界：不解析 JWT、不生成 HTTP 响应，不执行关闭工单操作。
"""
import logging
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from datetime import UTC, datetime
from ticketpilot.approvals import repository
from ticketpilot.approvals.models import ApprovalRecord
from ticketpilot.approvals.schemas import (
    ApprovalCreateRequest,
    ApprovalReviewRequest,
)
from ticketpilot.identity.models import UserRecord
from ticketpilot.audit import repository as audit_repository
from ticketpilot.audit.schemas import AuditEventInput
from typing import Literal

from ticketpilot.audit import service as audit_service
from ticketpilot.core.database import build_session_factory

logger = logging.getLogger(__name__)

class ApprovalAccessError(PermissionError):
    """当前用户不允许进行指定的审批业务操作。"""

class ApprovalTicketNotFoundError(LookupError):
    """申请关联的工单不存在。"""


class ApprovalConflictError(ValueError):
    """当前业务状态不允许执行指定审批操作。"""


class ApprovalStorageError(RuntimeError):
    """审批数据查询、写入或提交失败。"""

class ApprovalNotFoundError(LookupError):
    """指定审批申请不存在，或不在当前用户可见范围内。"""


def create_approval(
    *,
    session: Session,
    request: ApprovalCreateRequest,
    requester: UserRecord,
) -> ApprovalRecord:
    """创建申请并提交；失败时回滚业务，再独立记录失败尝试。"""
    # 当前项目的业务Session绑定Engine。
    # 从同一个Engine创建新Session工厂，避免写到其他数据库。
    audit_session_factory = build_session_factory(
        session.get_bind(),
    )

    # 在业务操作和rollback之前保存普通值。
    actor_id = requester.id
    actor_role = (
        requester.role
        if requester.role in ("agent", "supervisor", "admin")
        else "unknown"
    )
    target_ticket_id = request.ticket_id

    def record_creation_failure(
        *,
        outcome: Literal["denied", "failed"],
        error_code: Literal[
            "permission_denied",
            "not_found",
            "state_conflict",
            "storage_error",
        ],
    ) -> None:
        """先撤销业务事务，再独立记录本次创建失败。"""
        # 使用提前保存的普通值，不读取失败后的ORM对象。
        event = AuditEventInput(
            event_type=(
                "approval.denied"
                if outcome == "denied"
                else "approval.failed"
            ),
            actor_id=actor_id,
            actor_role=actor_role,
            ticket_id=target_ticket_id,
            outcome=outcome,
            error_code=error_code,
        )

        try:
            # 先撤销业务修改、结束原事务。
            session.rollback()

        except SQLAlchemyError:
            # 原事务未能正常清理，不继续尝试独立审计写入。
            # 返回后，外层仍然会抛出原来的业务错误。
            logger.error("创建审批失败后的事务回滚未能完成")
            return

        # 数据库写入失败会返回False。
        # 无论审计是否保存成功，都不替代原业务异常。
        audit_service.record_failed_attempt(
            event=event,
            session_factory=audit_session_factory,
        )

    # requester 必须由后端认证流程取得，不能来自请求体。
    if (
        not requester.is_active
        or requester.role not in ("agent", "supervisor", "admin")
    ):
        record_creation_failure(
            outcome="denied",
            error_code="permission_denied",
        )

        raise ApprovalAccessError(
            "当前用户不能提交审批申请"
        )

    try:
        # 请求模型中的 UUID 转成数据库使用的字符串编号。
        ticket = repository.get_ticket_for_approval(
            session,
            str(request.ticket_id),
        )

        if ticket is None:
            raise ApprovalTicketNotFoundError(
                "工单不存在"
            )

        if ticket.status == "closed":
            raise ApprovalConflictError(
                "工单已关闭，无需再次申请"
            )

        record = ApprovalRecord(
            # 审批记录本身的编号。
            id=str(uuid4()),

            # 本次操作的编号，后续执行及重试沿用它。
            operation_id=str(uuid4()),

            ticket_id=ticket.id,
            action=request.action,
            status="pending",

            # 从后端取得的用户对象中记录真实申请人。
            requested_by=requester.id,

            # 还没有发生审批和执行。
            reviewed_by=None,
            request_reason=request.request_reason,
            approval_reason=None,
            result=None,
            reviewed_at=None,
        )

        repository.add_approval(
            session,
            record,
        )
        # 申请与成功审计使用同一个 Session、同一个事务。
        audit_repository.add_event(
            session,
            AuditEventInput(
                event_type="approval.requested",
                actor_id=requester.id,
                actor_role=requester.role,
                approval_id=record.id,
                ticket_id=record.ticket_id,
                operation_id=record.operation_id,
                outcome="succeeded",
            ),
        )
        # 由 Service 决定整个创建流程何时提交。
        session.commit()

        return record
    except ApprovalTicketNotFoundError:
        record_creation_failure(
            outcome="failed",
            error_code="not_found",
        )

        # 继续抛出原来的“工单不存在”异常。
        raise

    except ApprovalConflictError:
        record_creation_failure(
            outcome="failed",
            error_code="state_conflict",
        )

        # 继续抛出原来的状态冲突异常。
        raise

    except SQLAlchemyError as error:
        record_creation_failure(
            outcome="failed",
            error_code="storage_error",
        )

        # 仍使用原来的业务异常与数据库异常链。
        raise ApprovalStorageError(
            "创建审批申请失败"
        ) from error

def get_approval(
    *,
    session: Session,
    approval_id: str,
    viewer: UserRecord,
) -> ApprovalRecord:
    """根据当前用户的角色，查询可见的审批申请。"""

    if (
        not viewer.is_active
        or viewer.role not in ("agent", "supervisor", "admin")
    ):
        raise ApprovalAccessError(
            "当前用户不能查看审批申请"
        )

    # 普通客服只能看自己；主管和管理员不限制申请人。
    requested_by = (
        viewer.id
        if viewer.role == "agent"
        else None
    )

    try:
        record = repository.get_approval_by_id(
            session,
            approval_id,
            requested_by=requested_by,
        )

    except SQLAlchemyError as error:
        session.rollback()
        raise ApprovalStorageError(
            "查询审批申请失败"
        ) from error

    if record is None:
        # 不存在和不在查询范围内，统一使用同一个业务错误。
        raise ApprovalNotFoundError(
            "审批申请不存在"
        )

    return record

def review_approval(
    *,
    session: Session,
    approval_id: str,
    request: ApprovalReviewRequest,
    reviewer: UserRecord,
) -> ApprovalRecord:
    """由主管或管理员提交一次审批决定。"""

    audit_session_factory = build_session_factory(session.get_bind())
    actor_id = reviewer.id
    actor_role = reviewer.role if reviewer.role in ("agent", "supervisor", "admin") else "unknown"
    # 没有合法读取到申请前，不补查工单或操作信息。
    target_ticket_id = None
    target_operation_id = None

    def record_review_failure(
        *,
        outcome: Literal["denied", "failed"],
        error_code: Literal["permission_denied", "not_found", "state_conflict", "storage_error"],
    ) -> None:
        """使用回滚前的普通值快照，独立保存本次审批尝试失败。"""
        event = AuditEventInput(
            event_type="approval.denied" if outcome == "denied" else "approval.failed",
            actor_id=actor_id, actor_role=actor_role, approval_id=approval_id,
            ticket_id=target_ticket_id, operation_id=target_operation_id,
            outcome=outcome, error_code=error_code,
        )
        try:
            session.rollback()
        except SQLAlchemyError:
            logger.error("审批决定失败后的事务回滚未能完成")
            return
        audit_service.record_failed_attempt(event=event, session_factory=audit_session_factory)

    if (
        not reviewer.is_active
        or reviewer.role not in ("supervisor", "admin")
    ):
        record_review_failure(outcome="denied", error_code="permission_denied")
        raise ApprovalAccessError(
            "只有主管或管理员可以审批"
        )

    try:
        # 已确认管理角色，允许查找所有申请人的记录。
        record = repository.get_approval_by_id(
            session,
            approval_id,
            requested_by=None,
        )

        if record is None:
            raise ApprovalNotFoundError(
                "审批申请不存在"
            )

        # 必须在任何可能失败的写入前保存快照；回滚后不再读取record。
        target_ticket_id = record.ticket_id
        target_operation_id = record.operation_id

        updated = repository.review_pending_approval(
            session,
            approval_id,
            reviewer_id=reviewer.id,
            approved=request.approved,
            reason=request.reason,
            reviewed_at=datetime.now(UTC),
        )

        if not updated:
            raise ApprovalConflictError(
                "该申请已处理，不能重复审批"
            )

        # 条件 UPDATE 没有同步 Session 中的旧对象。
        # 在提交前重新读取，取得最新决定、审批人和时间。
        session.refresh(record)
        # 批准和拒绝都是成功保存的审批决定。
        audit_repository.add_event(
            session,
            AuditEventInput(
                event_type=(
                    "approval.approved"
                    if request.approved
                    else "approval.rejected"
                ),
                actor_id=reviewer.id,
                actor_role=reviewer.role,
                approval_id=record.id,
                ticket_id=record.ticket_id,
                operation_id=record.operation_id,
                outcome="succeeded",
            ),
        )
        session.commit()
        return record

    except ApprovalNotFoundError:
        record_review_failure(outcome="failed", error_code="not_found")
        raise

    except ApprovalConflictError:
        record_review_failure(outcome="failed", error_code="state_conflict")
        raise

    except SQLAlchemyError as error:
        record_review_failure(outcome="failed", error_code="storage_error")
        raise ApprovalStorageError(
            "保存审批决定失败"
        ) from error
