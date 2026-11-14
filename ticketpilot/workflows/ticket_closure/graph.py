"""
文件职责：组装关闭工单流程的节点、连线与条件分支。
作用与配合：使用state.py的类型和nodes.py的节点，接收调用方的检查点保存器。
使用位置：由checkpoints.py创建可查询、暂停和恢复的正式图。
边界：只编排步骤，不直接写工单表、不解析HTTP身份。
"""

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ticketpilot.workflows.ticket_closure.nodes import (
    execute_ticket_closure,
    read_approval,
    wait_for_approval,
)
from ticketpilot.workflows.ticket_closure.state import (
    TicketClosureContext,
    TicketClosureState,
)


def choose_after_read(
    state: TicketClosureState,
) -> Literal["wait", "execute", "finish"]:
    """根据刚读取的审批状态，选择等待、执行或结束。"""

    if state["approval_status"] == "pending":
        return "wait"

    if state["approval_status"] == "approved":
        return "execute"

    # rejected、succeeded 等状态暂时直接结束，
    # 保留 read_approval 已读取的状态和结果。
    return "finish"


def build_workflow(
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """构建读取、等待、复查和正式执行的审批流程。"""

    builder = StateGraph(
        TicketClosureState,
        context_schema=TicketClosureContext,
    )

    builder.add_node("read_approval", read_approval)
    builder.add_node("wait_for_approval", wait_for_approval)
    builder.add_node("execute_ticket_closure", execute_ticket_closure)

    builder.add_edge(START, "read_approval")

    builder.add_conditional_edges(
        "read_approval",
        choose_after_read,
        {
            "wait": "wait_for_approval",
            "execute": "execute_ticket_closure",
            "finish": END,
        },
    )

    # 等待恢复后，重新查询实际审批状态。
    builder.add_edge("wait_for_approval", "read_approval")

    # 执行成功后，结束本次流程。
    builder.add_edge("execute_ticket_closure", END)

    return builder.compile(
        checkpointer=checkpointer,
    )
