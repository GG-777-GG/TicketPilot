"""
文件职责：组织关闭工单工作流的启动、恢复和失败重试。
作用与配合：复用查询服务、检查点管理和正式图。
使用位置：由工作流HTTP入口或Agent申请工具等可信后端调用方使用。
边界：不解析JWT，不直接写工单表；互斥仅覆盖当前进程。
"""

from pathlib import Path
import logging
from sqlite3 import Error as SQLiteError
from threading import Lock
from typing import Literal

from langgraph.types import Command
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ticketpilot.approvals import service
from ticketpilot.approvals.schemas import ApprovalWorkflowResponse
from ticketpilot.identity.models import UserRecord
from ticketpilot.audit import service as audit_service
from ticketpilot.audit.schemas import AuditEventInput
from ticketpilot.core.database import build_session_factory
from ticketpilot.workflows.ticket_closure.checkpoints import (
    open_ticket_closure_workflow,
)
from ticketpilot.workflows.ticket_closure.service import (
    get_workflow_status,
)
from ticketpilot.workflows.ticket_closure.state import (
    TicketClosureContext,
)


# 当前本地单worker版本串行处理工作流写操作。
_operation_lock = Lock()
logger = logging.getLogger(__name__)


def run_workflow(
    *,
    session: Session,
    approval_id: str,
    viewer: UserRecord,
    checkpoint_path: Path,
    action: Literal["start", "resume", "retry"],
    allow_execution: bool = True,
) -> ApprovalWorkflowResponse:
    """为编排入口补记失败，底层已尝试审计的同一错误不重复记录。"""
    actor_id = viewer.id
    actor_role = viewer.role if viewer.role in ("agent", "supervisor", "admin") else "unknown"
    audit_factory = build_session_factory(session.get_bind())
    # 由内部编排在合法读取申请后填入纯值，不额外补查业务记录。
    target = {}

    def record_outer_failure(error: Exception, *, code: str, denied: bool = False) -> None:
        """先清理原事务再尽力审计；标记含义是已尝试，不承诺持久化。"""
        if getattr(error, "_failure_audit_attempted", False):
            return
        error._failure_audit_attempted = True
        event = AuditEventInput(
            event_type="approval.denied" if denied else "approval.failed",
            actor_id=actor_id, actor_role=actor_role, approval_id=approval_id,
            ticket_id=target.get("ticket_id"), operation_id=target.get("operation_id"),
            outcome="denied" if denied else "failed", error_code=code,
        )
        try:
            session.rollback()
        except SQLAlchemyError:
            logger.error("工作流失败后的事务回滚未能完成")
            return
        audit_service.record_failed_attempt(event=event, session_factory=audit_factory)

    try:
        return _run_workflow(
            session=session, approval_id=approval_id, viewer=viewer,
            checkpoint_path=checkpoint_path, action=action,
            allow_execution=allow_execution, audit_target=target,
        )
    except service.ApprovalAccessError as error:
        record_outer_failure(error, code="permission_denied", denied=True)
        raise
    except service.ApprovalNotFoundError as error:
        record_outer_failure(error, code="not_found")
        raise
    except service.ApprovalConflictError as error:
        record_outer_failure(error, code="state_conflict")
        raise
    except service.ApprovalStorageError as error:
        code = "workflow_error" if isinstance(error.__cause__, (OSError, SQLiteError)) else "storage_error"
        record_outer_failure(error, code=code)
        raise
    except RuntimeError as error:
        # 不把节点原始异常正文交给HTTP；错误链仍保留给后台排查。
        record_outer_failure(error, code="workflow_error")
        wrapped = service.ApprovalStorageError("运行工作流节点失败")
        wrapped._failure_audit_attempted = True
        raise wrapped from error


def _run_workflow(
    *,
    session: Session,
    approval_id: str,
    viewer: UserRecord,
    checkpoint_path: Path,
    action: Literal["start", "resume", "retry"],
    allow_execution: bool,
    audit_target: dict[str, str],
) -> ApprovalWorkflowResponse:
    """检查动作条件，沿用原申请与会话运行工作流。"""

    if action not in ("start", "resume", "retry"):
        raise service.ApprovalConflictError("不支持的工作流操作")

    if (
        not viewer.is_active
        or viewer.role not in ("agent", "supervisor", "admin")
    ):
        raise service.ApprovalAccessError("当前用户不能操作工作流")

    # 恢复可能进入关闭节点，因此恢复和重试由管理角色执行。
    if action != "start" and viewer.role not in ("supervisor", "admin"):
        raise service.ApprovalAccessError("只有主管或管理员可以恢复或重试")

    if not _operation_lock.acquire(blocking=False):
        raise service.ApprovalConflictError("工作流正在处理中，请稍后查询")

    try:
        # 同时检查申请可见范围、检查点绑定和当前流程状态。
        before = get_workflow_status(
            session=session,
            approval_id=approval_id,
            viewer=viewer,
            checkpoint_path=checkpoint_path,
        )
        audit_target.update(
            ticket_id=str(before.approval.ticket_id),
            operation_id=str(before.approval.operation_id),
        )

        if action == "start":
            # 重复启动只返回已有进度，不重新提交初始State。
            if before.started:
                return before

            if before.approval.status != "pending":
                raise service.ApprovalConflictError(
                    "只有待审批申请可以启动新流程"
                )
        elif not before.started:
            raise service.ApprovalConflictError("工作流尚未启动")

        config = {
            "configurable": {
                "thread_id": before.thread_id,
            }
        }

        with open_ticket_closure_workflow(checkpoint_path) as workflow:
            snapshot = workflow.get_state(config)

            if action == "start":
                # 全部编号来自数据库中的申请，不接受客户端自报。
                graph_input = {
                    "approval_id": str(before.approval.id),
                    "operation_id": str(before.approval.operation_id),
                    "ticket_id": str(before.approval.ticket_id),
                    "approval_status": None,
                    "result": "",
                }

            elif action == "resume":
                if not any(task.interrupts for task in snapshot.tasks):
                    raise service.ApprovalConflictError(
                        "当前没有等待恢复的审批暂停点"
                    )

                # 只唤醒，真实审批决定仍由读取节点重新查询。
                graph_input = Command(resume={"recheck": True})

            else:
                has_error = any(
                    task.error is not None
                    for task in snapshot.tasks
                )
                allowed_node = snapshot.next in (
                    ("read_approval",),
                    ("execute_ticket_closure",),
                )

                if not has_error or not allowed_node:
                    raise service.ApprovalConflictError(
                        "当前没有允许重试的失败节点"
                    )

                graph_input = None

            workflow.invoke(
                graph_input,
                config=config,
                context=TicketClosureContext(
                    session=session,
                    viewer=viewer,
                    allow_execution=allow_execution,
                ),
            )

        # 节点可能通过条件UPDATE修改了业务数据。
        # 清除当前Session的旧对象缓存，再查询实时结果。
        session.expire_all()

        return get_workflow_status(
            session=session,
            approval_id=approval_id,
            viewer=viewer,
            checkpoint_path=checkpoint_path,
        )

    except (
        service.ApprovalAccessError,
        service.ApprovalNotFoundError,
        service.ApprovalConflictError,
        service.ApprovalStorageError,
    ):
        # PermissionError也是OSError子类；先保留业务异常及其审计标记，
        # 避免把权限拒绝误包装成检查点存储故障。
        raise

    except (OSError, SQLiteError) as error:
        raise service.ApprovalStorageError(
            "运行工作流检查点失败"
        ) from error

    finally:
        # 包括提前return和抛异常的情况，都必须释放锁。
        _operation_lock.release()
