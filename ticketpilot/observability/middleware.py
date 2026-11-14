"""
文件职责：为HTTP请求建立编号与真实SERVER Span，并回传关联编号。
作用与配合：app.py生命周期提供Provider，审计Repository读取当前上下文。
边界：不鉴权，不改正文，不收集令牌/正文/原始异常；暂不提取远端Trace父上下文。
"""

import logging
from asyncio import CancelledError
from contextlib import nullcontext

from opentelemetry.context import Context
from opentelemetry.trace import SpanKind, Status, StatusCode

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ticketpilot.observability.context import request_context
from ticketpilot.observability.tracing import get_current_trace_id
from ticketpilot.observability.spans import tracer_context


logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    """用纯ASGI包装完整请求，兼容异步处理、同步工作线程及流式响应。"""

    def __init__(self, app: ASGIApp) -> None:
        """保存下游应用，实例上不保存任何单次请求状态。"""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """每次调用独立设置并恢复上下文，非HTTP消息原样转发。"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        with request_context() as request_id:
            state = scope.setdefault("state", {})
            state["request_id"] = request_id
            application = scope.get("app")
            provider = getattr(getattr(application, "state", None), "tracer_provider", None)
            method = scope.get("method", "HTTP")
            manager = (
                provider.get_tracer("ticketpilot.http").start_as_current_span(
                    method, kind=SpanKind.SERVER, context=Context(),
                    record_exception=False, set_status_on_exception=False,
                ) if provider is not None else nullcontext(None)
            )
            # 业务Tracer与HTTP Tracer来自同一Provider；SDK负责当前Span父子关系。
            operation_tracer = provider.get_tracer("ticketpilot.operations") if provider is not None else None
            with tracer_context(operation_tracer), manager as span:
                # 未运行应用生命周期的独立ASGI测试仍可只使用请求编号。
                trace_id = get_current_trace_id() if span is not None else None
                state["trace_id"] = trace_id
                response_status = None
                if span is not None:
                    span.set_attribute("http.request.method", method)
                    span.set_attribute("ticketpilot.request_id", str(request_id))

                async def send_with_request_id(message: Message) -> None:
                    """仅改响应头并记录状态；正文分块原样转发。"""
                    nonlocal response_status
                    if message["type"] == "http.response.start":
                        response_status = message["status"]
                        headers = MutableHeaders(scope=message)
                        headers["X-Request-ID"] = str(request_id)
                        if trace_id is not None:
                            headers["X-Trace-ID"] = trace_id
                        if span is not None:
                            span.set_attribute("http.response.status_code", response_status)
                            if response_status >= 500:
                                span.set_status(Status(StatusCode.ERROR, "http_error"))
                    await send(message)

                try:
                    await self.app(scope, receive, send_with_request_id)
                except CancelledError:
                    if span is not None:
                        span.set_status(Status(StatusCode.ERROR, "cancelled"))
                    raise
                except Exception:
                    if span is not None:
                        if response_status is None:
                            span.set_attribute("http.response.status_code", 500)
                        span.set_status(Status(StatusCode.ERROR, "unhandled_error"))
                    raise
                finally:
                    # 只记录路由模板，不能把含编号、查询串的原始URL放入Span。
                    route_path = getattr(scope.get("route"), "path", None)
                    if span is not None and route_path is not None:
                        span.set_attribute("http.route", route_path)
                        span.update_name(f"{method} {route_path}")


async def unhandled_error_response(request: Request, error: Exception) -> JSONResponse:
    """外层未捕获异常的500响应仍带原编号，不把异常正文返回客户端。"""
    request_id = getattr(request.state, "request_id", None)
    trace_id = getattr(request.state, "trace_id", None)
    logger.error("请求处理失败", extra={
        "request_id": str(request_id) if request_id else None,
        "trace_id": trace_id,
    })
    headers = {"X-Request-ID": str(request_id)} if request_id else {}
    if trace_id:
        headers["X-Trace-ID"] = trace_id
    return JSONResponse(
        status_code=500,
        content={"detail": "服务暂时不可用"},
        headers=headers,
    )
