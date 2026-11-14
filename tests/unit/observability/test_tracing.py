"""
文件职责：验证学习者编写的Provider初始化、真实上下文和Console输出。
作用与配合：真实SDK、内存导出器，不调用HTTP或业务数据库。
边界：不设置全局Provider，不声明HTTP/Agent跨进程追踪完整。
"""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ticketpilot.observability.tracing import build_tracer_provider, get_current_trace_id


def test_no_active_span_has_no_trace_id():
    """没有有效上下文时返回None，不生成替代编号。"""
    assert get_current_trace_id() is None


def test_provider_uses_resource_exporter_and_always_on_sampling():
    """真实Span具有正确服务标识、采样标记并进入注入的导出器。"""
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(service_name=" ticketpilot-test ", exporter=exporter)
    try:
        with provider.get_tracer("ticketpilot.test").start_as_current_span("sample") as span:
            assert span.get_span_context().trace_flags.sampled
        saved, = exporter.get_finished_spans()
        assert saved.name == "sample" and saved.resource.attributes["service.name"] == "ticketpilot-test"
    finally:
        provider.shutdown()


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_service_name_is_rejected(name):
    """无有效服务名称时不创建Provider。"""
    with pytest.raises(ValueError):
        build_tracer_provider(service_name=name)


def test_builder_does_not_replace_global_provider():
    """不同应用可以独立拥有Provider，不污染进程全局追踪配置。"""
    original = trace.get_tracer_provider()
    provider = build_tracer_provider(exporter=InMemorySpanExporter())
    try:
        assert trace.get_tracer_provider() is original
    finally:
        provider.shutdown()


def test_nested_spans_share_trace_and_restore_current_context():
    """子Span有独立span_id，同trace_id；离开内层恢复父Span。"""
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(exporter=exporter)
    try:
        tracer = provider.get_tracer("ticketpilot.test")
        with tracer.start_as_current_span("parent") as parent:
            identifier = get_current_trace_id()
            assert len(identifier) == 32 and int(identifier, 16) == parent.get_span_context().trace_id
            with tracer.start_as_current_span("child") as child:
                assert get_current_trace_id() == identifier
                assert child.get_span_context().span_id != parent.get_span_context().span_id
            assert trace.get_current_span() is parent
        assert get_current_trace_id() is None
        child, parent = exporter.get_finished_spans()
        assert child.parent.span_id == parent.context.span_id
    finally:
        provider.shutdown()


def test_default_console_uses_stderr_not_protocol_stdout(capsys):
    """Console默认输出stderr，保留MCP stdout协议通道。"""
    provider = build_tracer_provider()
    try:
        with provider.get_tracer("ticketpilot.test").start_as_current_span("console-check"):
            pass
        assert provider.force_flush()
    finally:
        provider.shutdown()
    output = capsys.readouterr()
    assert output.out == "" and "console-check" in output.err
