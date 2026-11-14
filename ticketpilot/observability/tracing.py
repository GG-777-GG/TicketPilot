"""
文件职责：创建OpenTelemetry追踪配置，读取当前真实Trace编号。

作用与配合：
- 应用启动时创建TracerProvider。
- 请求和业务步骤通过Tracer创建Span。
- 审计记录通过get_current_trace_id关联当前Trace。
- 应用关闭时负责清理Provider。

边界：
- 导入时不初始化或导出数据。
- 不设置全局TracerProvider。
- 不生成假的Trace编号，不记录业务正文和令牌。
"""

import sys

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON


def build_tracer_provider(
    *,
    service_name: str = "ticketpilot",
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """创建追踪Provider，由应用生命周期负责最后关闭。"""

    service_name = service_name.strip()

    if not service_name:
        raise ValueError("追踪服务名称不能为空")

    # Resource描述产生追踪数据的服务，不描述某次请求。
    resource = Resource.create(
        {
            "service.name": service_name,
        }
    )

    # 演示阶段记录所有Span。
    # 不注册进程退出钩子，后续由应用生命周期明确关闭。
    provider = TracerProvider(
        resource=resource,
        sampler=ALWAYS_ON,
        shutdown_on_exit=False,
    )

    # 允许测试注入内存导出器。
    # 默认使用stderr，为MCP的stdio协议保留stdout。
    actual_exporter = (
        exporter
        if exporter is not None
        else ConsoleSpanExporter(out=sys.stderr)
    )

    provider.add_span_processor(
        SimpleSpanProcessor(actual_exporter)
    )

    return provider


def get_current_trace_id() -> str | None:
    """取得当前有效Span的Trace编号，没有有效上下文时返回None。"""

    span_context = (
        trace.get_current_span()
        .get_span_context()
    )

    if not span_context.is_valid:
        return None

    # Trace编号内部是整数，转换为补足32位的小写十六进制。
    return f"{span_context.trace_id:032x}"