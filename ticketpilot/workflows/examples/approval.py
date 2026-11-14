"""
文件职责：演示工作流暂停审批、接收决定和恢复执行。
使用方式：uv run python -m ticketpilot.workflows.examples.approval。
作用与配合：为后续 TicketPilot 工单审批流程提供机制示例。
边界：使用 SQLite 文件检查点，只模拟关闭工单，
仅连接本地检查点文件，不修改真实工单，也不实现审批人身份验证。
"""

from typing import Literal, TypedDict

import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from langgraph.runtime import Runtime

# 复用上一批写好的幂等操作。
from ticketpilot.workflows.examples.idempotency import (
    initialize_database,
    execute_once,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
)

# 编译后的工作流类型，用于函数参数标注。
from langgraph.graph.state import CompiledStateGraph

from langchain_core.runnables import RunnableConfig
# 解析命令行中的 start、approve 等参数。
import argparse

# 根据当前代码文件的位置，确定检查点文件路径。
from pathlib import Path

# 把检查点保存到 SQLite 文件。
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

@dataclass(frozen=True)
class ApprovalContext:
    """提供本次执行使用的模拟业务数据库位置。"""

    effects_database_path: Path

class ApprovalDecision(BaseModel):
    """验证外部提交的审批决定和理由。"""

    model_config = ConfigDict(
        # 不允许提交模型没有定义的字段。
        extra="forbid",
        # 去掉字符串两端空白，再进行长度检查。
        str_strip_whitespace=True,
    )

    # 必须是真正的 True 或 False。
    approved: StrictBool

    # 去掉两端空白后，理由长度必须在 1～500 之间。
    reason: str = Field(
        min_length=1,
        max_length=500,
    )


class ApprovalState(TypedDict):
    """保存本次模拟操作及审批结果。"""

    ticket_id: str

    # 创建操作时生成；之后审批、失败重试都沿用此编号。
    operation_id: str

    # None 表示尚未决定；True 批准；False 拒绝。
    approved: bool | None

    # 审批前为空字符串，审批后保存理由。
    approval_reason: str

    result: str


def request_approval(
        state: ApprovalState,
) -> dict[str, bool | str]:
    """等待结构化审批输入，验证后保存决定与理由。"""

    print("进入审批节点")

    raw_decision = interrupt(
        {
            "ticket_id": state["ticket_id"],
            "action": "模拟关闭工单",
            "message": "请提交审批决定和理由。",
        }
    )

    # 外部输入必须通过验证，才能进入后续分支。
    decision = ApprovalDecision.model_validate(
        raw_decision
    )

    return {
        "approved": decision.approved,
        "approval_reason": decision.reason,
    }


def choose_after_approval(
        state: ApprovalState,
) -> Literal["approved", "rejected"]:
    """根据已保存的决定，选择批准或拒绝分支。"""

    if state["approved"] is True:
        return "approved"

    return "rejected"


def execute_demo(
    state: ApprovalState,
    runtime: Runtime[ApprovalContext],
) -> dict[str, str]:
    """执行获批的模拟操作，通过操作编号防止重复生效。"""

    if state["approved"] is not True:
        raise ValueError("操作尚未获批")

    connection = sqlite3.connect(
        runtime.context.effects_database_path,
        timeout=10,
    )

    try:
        # 创建演示表；已有的数据会保留。
        initialize_database(connection)

        # 这个函数返回前，业务效果与操作结果已经一起提交。
        result = execute_once(
            connection,
            operation_id=state["operation_id"],
            ticket_id=state["ticket_id"],
        )

        # 读取计数，观察失败重试是否导致重复生效。
        row = connection.execute(
            """
            SELECT execution_count
            FROM demo_effects
            WHERE ticket_id = ?
            """,
            (state["ticket_id"],),
        ).fetchone()

        print("操作编号：", state["operation_id"])
        print("当前工单累计模拟执行次数：", row[0])

    finally:
        connection.close()

    return {
        "result": result,
    }

def cancel_demo(
        state: ApprovalState,
) -> dict[str, str]:
    """记录拒绝结果，结束本次模拟操作。"""

    return {
        "result": (
            f"已拒绝：取消工单 {state['ticket_id']} "
            "的模拟关闭操作。"
        ),
    }


def build_workflow(checkpointer: SqliteSaver):
    """连接审批和处理分支，并使用传入的 SQLite 检查点保存器。"""

    builder = StateGraph(
        ApprovalState,
        context_schema=ApprovalContext,
    )

    builder.add_node("request_approval", request_approval)
    builder.add_node("execute_demo", execute_demo)
    builder.add_node("cancel_demo", cancel_demo)

    builder.add_edge(START, "request_approval")

    builder.add_conditional_edges(
        "request_approval",
        choose_after_approval,
        {
            "approved": "execute_demo",
            "rejected": "cancel_demo",
        },
    )

    builder.add_edge("execute_demo", END)
    builder.add_edge("cancel_demo", END)

    return builder.compile(checkpointer=checkpointer)


def show_history(
        workflow: CompiledStateGraph,
        config: RunnableConfig,
) -> None:
    """按最新到最早的顺序，打印指定会话的检查点历史。"""

    history = workflow.get_state_history(config)

    for number, snapshot in enumerate(history, start=1):
        # 每个检查点都有自己的编号。
        checkpoint_id = (
            snapshot.config["configurable"]["checkpoint_id"]
        )

        print(f"第 {number} 条快照")
        print("检查点编号：", checkpoint_id)
        print("保存时间：", snapshot.created_at)

        # 早期快照可能还没有完整的业务字段。
        print(
            "工单编号：",
            snapshot.values.get("ticket_id", "尚未写入"),
        )
        print(
            "审批决定：",
            snapshot.values.get("approved"),
        )
        print(
            "审批理由：",
            snapshot.values.get("approval_reason", ""),
        )
        print(
            "执行结果：",
            snapshot.values.get("result", ""),
        )

        print("待执行节点：", snapshot.next)

        # 查看这个快照中的任务是否带有审批暂停信息。
        waiting = any(
            task.interrupts
            for task in snapshot.tasks
        )
        print("是否存在暂停信息：", waiting)

        print("-" * 40)


def main() -> None:
    """根据命令提交、查看或恢复已保存的模拟审批流程。"""

    parser = argparse.ArgumentParser(
        description="TicketPilot 持久化审批演示",
    )

    # 必填的位置参数，例如 start 或 approve。
    parser.add_argument(
        "action",
        choices=[
            "start",
            "status",
            "history",
            "approve",
            "reject",
            "retry",
        ],
    )

    # 必须明确指定操作哪个会话。
    parser.add_argument(
        "--thread",
        required=True,
        help="审批会话编号，例如 approval-demo-2",
    )

    parser.add_argument(
        "--reason",
        help="批准或拒绝时填写的审批理由",
    )

    args = parser.parse_args()

    config: RunnableConfig = {
        "configurable": {
            "thread_id": args.thread,
        },
    }

    # 当前文件在 ticketpilot/workflows/examples 下。
    # parents[3] 对应项目根目录。
    project_root = Path(__file__).resolve().parents[3]

    database_path = (
            project_root / "data" / "approval_checkpoints.sqlite"
    )

    # 如果 data 目录尚不存在，先创建它。
    database_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    runtime_context = ApprovalContext(
        effects_database_path=(
            project_root / "data" / "approval_effects.sqlite"
        ),
    )

    # 打开同一个文件，读取之前运行留下的检查点。
    with SqliteSaver.from_conn_string(
            str(database_path)
    ) as checkpointer:
        workflow = build_workflow(checkpointer)
        snapshot = workflow.get_state(config)

        if args.action == "start":
            # 避免把新的演示覆盖到已有会话上。
            if snapshot.values:
                print("这个会话已经存在，请查看状态或换一个编号。")
                return

            initial_state: ApprovalState = {
                "ticket_id": "DEMO-001",
                # 只在创建这次操作时生成一次。
                "operation_id": str(uuid4()),
                "approved": None,
                "approval_reason": "",
                "result": "",
            }


            paused = workflow.invoke(
                initial_state,
                config=config,
                context=runtime_context,
            )

            print(
                "待审批信息：",
                paused["__interrupt__"][0].value,
            )
            print("会话编号：", args.thread)
            print("已保存检查点，现在可以退出程序。")
            return

        # 查看或恢复的前提是：这个会话已经存在。
        if not snapshot.values:
            print("没有找到这个会话，请先执行 start。")
            return

        if args.action == "history":
            show_history(
                workflow=workflow,
                config=config,
            )
            return

        if args.action == "status":
            print("会话编号：", args.thread)
            print("保存的状态：", snapshot.values)
            print("待执行节点：", snapshot.next)

            # 显示当前检查点里记录的任务错误。
            for task in snapshot.tasks:
                if task.error is not None:
                    print("失败节点：", task.name)
                    print("错误信息：", task.error)

            return

        # 本例只有 request_approval 节点会等待人工决定。
        # tasks 中的 interrupts 可以确认它确实处于审批暂停。
        waiting_for_approval = any(
            task.interrupts
            for task in snapshot.tasks
        )

        if args.action == "retry":
            # 人工审批必须通过 approve/reject 提交决定。
            if waiting_for_approval:
                print("当前正在等待审批，请先批准或拒绝。")
                return

            # 任务上的 error 记录节点执行失败的信息。
            has_error = any(
                task.error is not None
                for task in snapshot.tasks
            )

            if not snapshot.next or not has_error:
                print("当前没有需要重试的失败节点。")
                return

            # 本批只允许重试已经获批的模拟执行节点。
            if (
                snapshot.next != ("execute_demo",)
                or snapshot.values.get("approved") is not True
            ):
                print("当前失败不属于已批准的模拟执行，请先排查。")
                return

            # 不传新输入，继续检查点中的未完成工作。
            result = workflow.invoke(
                None,
                config=config,
                context=runtime_context,
            )

            print("重试结果：", result["result"])
            print("审批理由：", result["approval_reason"])
            print(
                "待执行节点：",
                workflow.get_state(config).next,
            )
            return

        if not waiting_for_approval:
            print("当前没有等待审批的暂停点。")
            print("已有结果：", snapshot.values["result"])
            return

        # 到这里，action 只能是 approve 或 reject。
        # 先验证参数，避免把错误输入送进暂停中的工作流。
        try:
            decision = ApprovalDecision(
                approved=args.action == "approve",
                reason=args.reason,
            )
        except ValidationError:
            parser.error(
                "审批理由不能为空，去掉两端空白后长度应为 1～500。"
            )

        # 把验证后的模型转换成普通字典，再传给恢复命令。
        result = workflow.invoke(
            Command(
                resume=decision.model_dump(mode="json"),
            ),
            config=config,
            context=runtime_context,
        )

        print("最终结果：", result["result"])
        print("审批理由：", result["approval_reason"])
        print(
            "待执行节点：",
            workflow.get_state(config).next,
        )


if __name__ == "__main__":
    main()
