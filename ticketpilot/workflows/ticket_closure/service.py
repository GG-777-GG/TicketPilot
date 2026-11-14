"""
文件职责：为 HTTP 入口提供正式工单工作流的编排服务。
作用与配合：复用审批权限查询，以及文件检查点管理模块。
当前阶段：查询审批实时数据和工作流进度。
边界：不解析 JWT，不返回原始异常，不执行关闭操作。
"""

from pathlib import Path
from sqlite3 import Error as SQLiteError

from sqlalchemy.orm import Session

from ticketpilot.workflows.ticket_closure.checkpoints import (
    open_ticket_closure_workflow,
)
from ticketpilot.approvals import service
from ticketpilot.approvals.schemas import (
    ApprovalResponse,
    ApprovalWorkflowResponse,
)
from ticketpilot.identity.models import UserRecord


def get_workflow_status(
    *,
    session: Session,
    approval_id: str,
    viewer: UserRecord,
    checkpoint_path: Path,
) -> ApprovalWorkflowResponse:
    """先检查审批查询权限，再读取对应流程的检查点。"""

    # 普通客服只能查看本人申请，管理角色可以查看其他申请。
    # 权限不足时在这里结束，不继续读取检查点文件。
    record = service.get_approval(
        session=session,
        approval_id=approval_id,
        viewer=viewer,
    )

    approval = ApprovalResponse.model_validate(
        record,
        from_attributes=True,
    )

    # 同一操作始终定位到同一条流程，不让客户端任意选择会话。
    thread_id = f"ticket-closure:{approval.operation_id}"
    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    # 文件或会话尚不存在时，返回“未启动”。
    values = {}
    next_nodes = []
    waiting = False
    has_error = False

    try:
        # 查询本身不负责创建新的检查点文件。
        if checkpoint_path.exists():
            with open_ticket_closure_workflow(
                checkpoint_path,
            ) as workflow:
                snapshot = workflow.get_state(config)

                values = snapshot.values
                next_nodes = list(snapshot.next)
                waiting = any(
                    task.interrupts
                    for task in snapshot.tasks
                )
                has_error = any(
                    task.error is not None
                    for task in snapshot.tasks
                )

    except (OSError, SQLiteError) as error:
        raise service.ApprovalStorageError(
            "查询工作流检查点失败"
        ) from error

    # 已有检查点必须与当前申请、目标工单及操作一致。
    expected_ids = {
        "approval_id": str(approval.id),
        "operation_id": str(approval.operation_id),
        "ticket_id": str(approval.ticket_id),
    }

    if values and any(
        values.get(key) != expected
        for key, expected in expected_ids.items()
    ):
        raise service.ApprovalConflictError(
            "工作流检查点与审批申请不一致"
        )

    return ApprovalWorkflowResponse(
        approval=approval,
        thread_id=thread_id,
        started=bool(values),
        checkpoint_status=values.get("approval_status"),
        next_nodes=next_nodes,
        waiting_for_approval=waiting,
        has_error=has_error,
    )