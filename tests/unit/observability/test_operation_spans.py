"""
文件职责：验证学习者的子Span包装及Tracer作用域、错误、取消和工具名脱敏。
作用与配合：真实SDK与内存导出器；不调用模型、网络或数据库。
边界：不设置全局Provider，不声明跨MCP进程传播。
"""

import asyncio

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from ticketpilot.observability.spans import operation_span, tracer_context
from ticketpilot.observability.tracing import build_tracer_provider, get_current_trace_id


@pytest.fixture
def tracing():
    """每项测试独立创建并关闭Provider。"""
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(exporter=exporter)
    try:
        yield provider.get_tracer("ticketpilot.test"), exporter
    finally:
        provider.shutdown()


def test_without_tracer_executes_original_body():
    """无追踪配置时仍执行业务，不生成替代Trace。"""
    with operation_span("agent.run") as span:
        assert span is None and get_current_trace_id() is None


def test_without_tracer_preserves_original_exception():
    """无Tracer时也不吞掉原错误。"""
    original = RuntimeError("测试故障")
    with pytest.raises(RuntimeError) as captured:
        with operation_span("agent.run"):
            raise original
    assert captured.value is original


@pytest.mark.parametrize("name", ["untrusted-step-marker", ""])
def test_invalid_step_name_is_not_exported(tracing, name):
    """只允许固定步骤，拒绝把任意用户字符串用作Span名称。"""
    tracer, exporter = tracing
    with tracer_context(tracer), pytest.raises(ValueError):
        with operation_span(name):
            raise AssertionError("不应执行业务")
    assert exporter.get_finished_spans() == ()


def test_nested_operation_spans_share_trace_and_restore_parent(tracing):
    """同一Trace下不同步骤有独立Span编号和正确父级。"""
    tracer, exporter = tracing
    with tracer_context(tracer):
        with operation_span("agent.run") as parent:
            identifier = get_current_trace_id()
            with operation_span("model.decide") as child:
                assert get_current_trace_id() == identifier
                assert child.get_span_context().span_id != parent.get_span_context().span_id
            assert trace.get_current_span() is parent
    child, parent = exporter.get_finished_spans()
    assert child.parent.span_id == parent.context.span_id
    assert child.kind == parent.kind == SpanKind.INTERNAL
    assert get_current_trace_id() is None


def test_nested_tracer_context_restores_previous_application(tracing):
    """临时切换Tracer后恢复外层，离开作用域不保留应用对象。"""
    tracer, exporter = tracing
    other_exporter = InMemorySpanExporter()
    provider = build_tracer_provider(exporter=other_exporter)
    try:
        with tracer_context(tracer):
            with tracer_context(provider.get_tracer("other")):
                with operation_span("model.decide"):
                    pass
            with operation_span("agent.run"):
                pass
        with operation_span("agent.run") as absent:
            assert absent is None
        assert [span.name for span in exporter.get_finished_spans()] == ["agent.run"]
        assert [span.name for span in other_exporter.get_finished_spans()] == ["model.decide"]
    finally:
        provider.shutdown()


def test_operation_failure_sets_fixed_status_without_raw_exception(tracing):
    """错误对象原样传播，但异常正文/堆栈不进入Span。"""
    tracer, exporter = tracing
    original = RuntimeError("private-operation-marker")
    with tracer_context(tracer), pytest.raises(RuntimeError) as captured:
        with operation_span("model.decide"):
            raise original
    saved, = exporter.get_finished_spans()
    assert captured.value is original
    assert saved.status.status_code == StatusCode.ERROR and saved.status.description == "operation_failed"
    assert not saved.events and "private-operation-marker" not in saved.to_json()


def test_cancellation_remains_cancellation_and_ends_span(tracing):
    """取消不会改成普通业务错误，且Span与Tracer上下文均清理。"""
    tracer, exporter = tracing

    async def scenario():
        started = asyncio.Event()
        restored = []

        async def work():
            try:
                with tracer_context(tracer), operation_span("mcp.client"):
                    started.set()
                    await asyncio.Event().wait()
            finally:
                with operation_span("agent.run") as missing:
                    restored.append((get_current_trace_id(), missing))

        task = asyncio.create_task(work())
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert restored == [(None, None)]

    asyncio.run(scenario())
    saved, = exporter.get_finished_spans()
    assert saved.status.description == "cancelled" and not saved.events


@pytest.mark.parametrize("name", ["get_ticket_details", "search_knowledge", "request_ticket_closure", "private-tool-marker"])
def test_tool_name_is_bounded_to_known_values(tracing, name):
    """未知工具只导出unknown，不把模型原始名称当属性。"""
    tracer, exporter = tracing
    with tracer_context(tracer), operation_span("tool.execute", tool_name=name):
        pass
    saved, = exporter.get_finished_spans()
    assert saved.attributes["ticketpilot.tool.name"] == ("unknown" if name == "private-tool-marker" else name)
    assert "private-tool-marker" not in saved.to_json()


@pytest.mark.parametrize("name,kind", [("mcp.client", SpanKind.CLIENT), ("tool.execute", SpanKind.INTERNAL)])
def test_span_kind_matches_dependency_or_internal_step(tracing, name, kind):
    """依赖调用标CLIENT，其余业务步骤标INTERNAL。"""
    tracer, exporter = tracing
    with tracer_context(tracer), operation_span(name):
        pass
    saved, = exporter.get_finished_spans()
    assert saved.kind == kind
