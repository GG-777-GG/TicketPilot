"""
文件职责：提供当前应用Tracer，以及业务步骤的安全Span包装。

作用与配合：
- HTTP入口通过tracer_context提供当前应用的Tracer。
- Agent、模型和工具通过operation_span记录步骤。
- SDK根据当前Span上下文建立父子关系。

边界：
- 不创建或关闭TracerProvider。
- 不设置全局Provider。
- 不记录问题、正文、令牌、工具参数或原始异常。
- 不负责MCP Server的跨进程上下文传播。
"""

from asyncio import CancelledError
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry.trace import (
    Span,
    SpanKind,
    Status,
    StatusCode,
    Tracer,
)


_current_tracer: ContextVar[Tracer | None] = ContextVar(
    "ticketpilot_current_tracer",
    default=None,
)

# 固定步骤名称，避免把用户输入作为Span名称。
_SPAN_NAMES = (
    "agent.run",
    "model.decide",
    "tool.execute",
    "mcp.client",
)

# 工具名也使用固定集合，未知名称不原样导出。
_TOOL_NAMES = (
    "get_ticket_details",
    "search_knowledge",
    "request_ticket_closure",
)


@contextmanager
def tracer_context(
    tracer: Tracer | None,
) -> Iterator[None]:
    """在作用域内提供当前应用Tracer，退出时恢复原值。"""

    token = _current_tracer.set(tracer)

    try:
        yield
    finally:
        _current_tracer.reset(token)


@contextmanager
def operation_span(
    name: str,
    *,
    tool_name: str | None = None,
) -> Iterator[Span | None]:
    """记录一个固定名称的步骤，异常脱敏后继续向外抛出。"""

    if name not in _SPAN_NAMES:
        raise ValueError("不支持的追踪步骤名称")

    tracer = _current_tracer.get()

    # 独立脚本或未启用追踪的测试仍然正常执行业务。
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(
        name,
        kind=SpanKind.CLIENT if name == "mcp.client" else SpanKind.INTERNAL,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        if tool_name is not None:
            safe_tool_name = (
                tool_name
                if tool_name in _TOOL_NAMES
                else "unknown"
            )

            span.set_attribute(
                "ticketpilot.tool.name",
                safe_tool_name,
            )

        try:
            yield span

        except CancelledError:
            # 取消属于独立的控制流程，不能吞掉。
            span.set_status(
                Status(StatusCode.ERROR, "cancelled")
            )
            raise

        except Exception:
            # 只记录固定类别，不记录异常正文和堆栈。
            span.set_status(
                Status(StatusCode.ERROR, "operation_failed")
            )
            raise
