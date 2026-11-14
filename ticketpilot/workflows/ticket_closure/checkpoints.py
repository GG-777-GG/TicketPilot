"""
文件职责：管理正式关闭工单工作流的 SQLite 检查点连接。
作用与配合：打开检查点文件，调用 build_workflow 组装工作流。
使用方式：调用方通过 with 获取工作流，在代码块内查询或运行。
边界：不创建审批、不管理业务 Session、不验证 HTTP 身份。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph

from ticketpilot.workflows.ticket_closure.graph import build_workflow


@contextmanager
def open_ticket_closure_workflow(
    checkpoint_path: Path,
) -> Iterator[CompiledStateGraph]:
    """打开文件检查点，在使用结束后关闭对应连接。"""

    # 转换成绝对路径，明确本次实际使用哪个文件。
    database_path = checkpoint_path.resolve()

    # 首次使用时创建父目录，已有目录会保留。
    database_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # 文件不存在时创建；存在时读取已有检查点。
    with SqliteSaver.from_conn_string(
        str(database_path),
    ) as checkpointer:
        workflow = build_workflow(
            checkpointer=checkpointer,
        )

        # 把工作流交给外层 with 代码块使用。
        # 外层使用结束后，退出内部 with，关闭 SQLite 连接。
        yield workflow