"""
文件职责：实现关闭工单流程的读取、等待和正式执行节点。
作用与配合：复用approvals的权限查询、事务和幂等执行服务。
使用位置：由graph.py注册为LangGraph节点。
边界：不组装图、不管理检查点连接、不解析HTTP身份。
"""

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from ticketpilot.approvals import execution, service
from ticketpilot.approvals.schemas import ApprovalResponse
from ticketpilot.workflows.ticket_closure.state import (
    TicketClosureContext,
    TicketClosureState,
)


def read_approval(
    state: TicketClosureState,
    runtime: Runtime[TicketClosureContext],
) -> dict[str, str]:
    """按当前用户权限查询申请，将普通数据写入工作流状态。"""

    # 复用已有查询服务，保留普通客服只能查看本人申请等规则。
    record = service.get_approval(
        session=runtime.context.session,
        approval_id=state["approval_id"],
        viewer=runtime.context.viewer,
    )

    # 校验 ORM 数据，并转换成已有的响应模型。
    approval = ApprovalResponse.model_validate(
        record,
        from_attributes=True,
    )

    # State 中只放编号、状态、文字，不放 ORM 对象。
    return {
        "approval_id": str(approval.id),
        "operation_id": str(approval.operation_id),
        "ticket_id": str(approval.ticket_id),
        "approval_status": approval.status,
        "result": approval.result or "",
    }


def wait_for_approval(
    state: TicketClosureState,
) -> dict[str, str]:
    """暂停等待；恢复后重新查询，不直接接受外部审批结论。"""

    interrupt(
        {
            "approval_id": state["approval_id"],
            "ticket_id": state["ticket_id"],
            "message": "申请尚未审批，请审批后恢复流程。",
        }
    )

    # 恢复输入只用于唤醒流程，不写入审批状态。
    # 返回后，图会重新进入 read_approval。
    return {}


def execute_ticket_closure(
    state: TicketClosureState,
    runtime: Runtime[TicketClosureContext],
) -> dict[str, str]:
    """通过正式执行服务关闭工单，并更新工作流状态。"""
    if not runtime.context.allow_execution:
        raise service.ApprovalAccessError(
            "本次运行仅允许发起申请，不能执行关闭"
        )

    # 只传申请编号和当前用户。
    # Service 会重新读取申请，检查权限、审批状态和已有执行结果。
    record = execution.execute_approval(
        session=runtime.context.session,
        approval_id=state["approval_id"],
        executor=runtime.context.viewer,
    )

    # Service 已经完成事务提交，再把结果转换成普通数据。
    approval = ApprovalResponse.model_validate(
        record,
        from_attributes=True,
    )

    return {
        "approval_id": str(approval.id),
        "operation_id": str(approval.operation_id),
        "ticket_id": str(approval.ticket_id),
        "approval_status": approval.status,
        "result": approval.result or "",
    }
