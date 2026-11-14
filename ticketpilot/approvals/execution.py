"""
文件职责：执行已批准的关闭工单申请，并提供幂等结果。
作用与配合：调用审批 Repository，在同一个事务中修改工单、
保存成功记录、更新申请状态；由审批执行路由调用。
边界：不解析 JWT、不处理 HTTP，不调用外部通知或支付服务。
"""

from datetime import UTC, datetime
import logging
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ticketpilot.approvals import repository
from ticketpilot.approvals.models import (
    ApprovalExecutionRecord,
    ApprovalRecord,
)
from ticketpilot.approvals.service import (
    ApprovalAccessError,
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalStorageError,
)
from ticketpilot.identity.models import UserRecord
from ticketpilot.audit import repository as audit_repository
from ticketpilot.audit.schemas import AuditEventInput
from ticketpilot.audit import service as audit_service
from ticketpilot.core.database import build_session_factory


logger = logging.getLogger(__name__)

def execute_approval(
    *,
    session: Session,
    approval_id: str,
    executor: UserRecord,
) -> ApprovalRecord:
    """执行已批准申请；重复成功请求返回原结果。"""

    audit_session_factory = build_session_factory(session.get_bind())
    actor_id = executor.id
    actor_role = executor.role if executor.role in ("agent", "supervisor", "admin") else "unknown"
    target_ticket_id = None
    target_operation_id = None

    def record_execution_failure(
        *,
        outcome: Literal["denied", "failed"],
        error_code: Literal["permission_denied", "not_found", "state_conflict", "storage_error"],
    ) -> None:
        """清理原事务后保存失败尝试，不读取过期ORM或重新关闭工单。"""
        event = AuditEventInput(
            event_type="approval.denied" if outcome == "denied" else "approval.failed",
            actor_id=actor_id, actor_role=actor_role, approval_id=approval_id,
            ticket_id=target_ticket_id, operation_id=target_operation_id,
            outcome=outcome, error_code=error_code,
        )
        try:
            session.rollback()
        except SQLAlchemyError:
            logger.error("审批执行失败后的事务回滚未能完成")
            return
        audit_service.record_failed_attempt(event=event, session_factory=audit_session_factory)

    if (
        not executor.is_active
        or executor.role not in ("supervisor", "admin")
    ):
        record_execution_failure(outcome="denied", error_code="permission_denied")
        error = ApprovalAccessError(
            "只有主管或管理员可以执行"
        )
        # 表示本层已尝试审计，不代表事件一定已保存。
        error._failure_audit_attempted = True
        raise error

    try:
        record = repository.get_approval_by_id(
            session,
            approval_id,
            requested_by=None,
        )

        if record is None:
            raise ApprovalNotFoundError(
                "审批申请不存在"
            )

        # 只保存实际读到的目标，不为权限失败或不存在的申请额外查询。
        target_ticket_id = record.ticket_id
        target_operation_id = record.operation_id

        now = datetime.now(UTC)

        claimed = repository.claim_approved_request(
            session,
            record.id,
            now,
        )

        # 条件更新后，重新读取数据库中的当前状态。
        session.refresh(record)

        saved = repository.get_execution(
            session,
            record.operation_id,
        )

        if not claimed:
            # 已完成的同一操作，可以直接返回原结果。
            if record.status == "succeeded" and saved is not None:
                # 防止记录不一致时错误复用其他目标的结果。
                if (
                    saved.ticket_id != record.ticket_id
                    or saved.action != record.action
                    or saved.result != record.result
                ):
                    raise ApprovalConflictError(
                        "执行记录与申请不一致"
                    )

                # 复用原执行结果，单独记录本次重放。
                # 不再次关闭工单，也不新增执行成功凭据。
                audit_repository.add_event(
                    session,
                    AuditEventInput(
                        event_type="execution.replayed",
                        actor_id=executor.id,
                        actor_role=executor.role,
                        approval_id=record.id,
                        ticket_id=record.ticket_id,
                        operation_id=record.operation_id,
                        outcome="replayed",
                    ),
                )

                session.commit()
                return record

            raise ApprovalConflictError(
                "申请未获批或当前状态不允许执行"
            )

        # approved 应当具有真实的审批信息，
        # 同时不能存在与执行状态矛盾的成功记录。
        if (
            saved is not None
            or record.action != "close_ticket"
            or record.reviewed_by is None
            or record.reviewed_at is None
        ):
            raise ApprovalConflictError(
                "审批或执行记录不完整，不能执行"
            )

        closed = repository.close_ticket_for_approval(
            session,
            record.ticket_id,
            now,
        )

        if not closed:
            raise ApprovalConflictError(
                "工单不存在或当前状态不允许关闭"
            )

        result = f"工单 {record.ticket_id} 已关闭。"

        execution_record = ApprovalExecutionRecord(
            operation_id=record.operation_id,
            ticket_id=record.ticket_id,
            executed_by=executor.id,
            action=record.action,
            result=result,
        )

        record.status = "succeeded"
        record.result = result
        record.updated_at = now

        # flush 会发送结果INSERT和申请状态UPDATE，
        # 前面的工单UPDATE也仍在同一个事务中。
        repository.add_execution(
            session,
            execution_record,
        )
        # 工单关闭、执行凭据、申请状态和成功审计一起提交。
        audit_repository.add_event(
            session,
            AuditEventInput(
                event_type="ticket.closed",
                actor_id=executor.id,
                actor_role=executor.role,
                approval_id=record.id,
                ticket_id=record.ticket_id,
                operation_id=record.operation_id,
                outcome="succeeded",
            ),
        )
        session.commit()
        return record

    except ApprovalNotFoundError as error:
        record_execution_failure(outcome="failed", error_code="not_found")
        error._failure_audit_attempted = True
        raise

    except ApprovalConflictError as error:
        record_execution_failure(outcome="failed", error_code="state_conflict")
        error._failure_audit_attempted = True
        raise

    except SQLAlchemyError as error:
        record_execution_failure(outcome="failed", error_code="storage_error")
        wrapped = ApprovalStorageError(
            "执行审批操作失败"
        )
        wrapped._failure_audit_attempted = True
        raise wrapped from error
