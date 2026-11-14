"""
文件职责：验证HTTP真实Span、生命周期、脱敏、并发和异常清理。
作用与配合：真实应用与SDK、内存导出器；测试路由只存在于测试应用。
边界：不访问业务库/模型，不宣称Agent子Span或MCP Server传播已实现。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from ticketpilot.app import create_app
from ticketpilot.observability.context import get_request_id
from ticketpilot.observability.middleware import RequestContextMiddleware
from ticketpilot.observability.tracing import build_tracer_provider, get_current_trace_id


pytestmark = pytest.mark.integration


@pytest.fixture
def traced_app():
    """不连接数据库的测试端点，仍使用正式生命周期和HTTP中间件。"""
    exporter = InMemorySpanExporter()
    app = create_app(span_exporter=exporter)

    @app.get("/test/sync/{item_id}")
    def sync(item_id: str):
        """工作线程中读取真实Span上下文。"""
        return {"trace_id": get_current_trace_id()}

    @app.get("/test/async/{item_id}")
    async def asynchronous(item_id: str):
        """异步路由中读取真实Span上下文。"""
        return {"trace_id": get_current_trace_id()}

    @app.get("/test/status/{code}")
    def status(code: int):
        """控制已处理HTTP状态。"""
        if code != 200:
            raise HTTPException(status_code=code, detail="测试响应")
        return {"ok": True}

    @app.get("/test/unhandled")
    def unhandled():
        """模拟不能向Span或客户端输出正文的异常。"""
        raise RuntimeError("private-exception-marker")

    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client, exporter


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_response_and_current_span_share_real_trace_id(traced_app, kind):
    """路由取得的编号、响应头及导出Span完全对应，模板不带真实编号。"""
    _, client, exporter = traced_app
    response = client.get(f"/test/{kind}/private-object")
    identifier = response.headers["X-Trace-ID"]
    assert response.status_code == 200 and len(identifier) == 32
    assert response.json()["trace_id"] == identifier
    saved, = exporter.get_finished_spans()
    assert format(saved.context.trace_id, "032x") == identifier
    assert saved.parent is None and saved.kind == SpanKind.SERVER
    assert saved.attributes["ticketpilot.request_id"] == response.headers["X-Request-ID"]
    assert saved.attributes["http.route"] == f"/test/{kind}/{{item_id}}"
    assert saved.name == f"GET /test/{kind}/{{item_id}}"
    assert get_current_trace_id() is None and get_request_id() is None


@pytest.mark.parametrize("code", [200, 403, 500])
def test_server_span_status_distinguishes_client_rejection_and_server_error(traced_app, code):
    """4xx业务拒绝不自动标服务器故障；5xx标记固定错误类别。"""
    _, client, exporter = traced_app
    response = client.get(f"/test/status/{code}")
    assert response.status_code == code
    saved, = exporter.get_finished_spans()
    assert saved.attributes["http.response.status_code"] == code
    assert saved.status.status_code == (StatusCode.ERROR if code == 500 else StatusCode.UNSET)
    assert not saved.events


def test_unhandled_error_keeps_trace_header_without_exporting_exception(traced_app):
    """上下文退出后外层500仍回传原Trace编号，Span不含异常正文/堆栈。"""
    _, client, exporter = traced_app
    response = client.get("/test/unhandled")
    assert response.status_code == 500 and response.json() == {"detail": "服务暂时不可用"}
    saved, = exporter.get_finished_spans()
    assert response.headers["X-Trace-ID"] == format(saved.context.trace_id, "032x")
    assert saved.status.status_code == StatusCode.ERROR and saved.status.description == "unhandled_error"
    assert saved.attributes["http.response.status_code"] == 500
    assert not saved.events and "private-exception-marker" not in saved.to_json()
    assert get_current_trace_id() is None


def test_parallel_http_requests_have_separate_root_spans(traced_app):
    """真实同步线程池并发，两个请求不串Trace或请求编号。"""
    app, client, exporter = traced_app
    barrier = Barrier(2, timeout=10)

    @app.get("/test/parallel")
    def parallel():
        """在重叠期间再次读取上下文。"""
        before = get_current_trace_id()
        barrier.wait()
        return {"before": before, "after": get_current_trace_id()}

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result(timeout=15) for future in [pool.submit(client.get, "/test/parallel"), pool.submit(client.get, "/test/parallel")]]
    identifiers = {response.headers["X-Trace-ID"] for response in responses}
    assert len(identifiers) == 2
    for response in responses:
        identifier = response.headers["X-Trace-ID"]
        assert response.json() == {"before": identifier, "after": identifier}
    assert len(exporter.get_finished_spans()) == 2
    assert all(saved.parent is None for saved in exporter.get_finished_spans())


def test_sensitive_inputs_are_not_exported(traced_app):
    """HTTP追踪只使用固定属性，不收集原始URL、令牌、正文或远端父上下文。"""
    _, client, exporter = traced_app
    supplied_trace = "11111111111111111111111111111111"
    response = client.get("/test/sync/private-object-marker?secret=private-query-marker", headers={
        "Authorization": "Bearer private-token-marker",
        "traceparent": f"00-{supplied_trace}-2222222222222222-01",
    })
    assert response.status_code == 200 and response.headers["X-Trace-ID"] != supplied_trace
    saved, = exporter.get_finished_spans()
    assert saved.parent is None
    text = saved.to_json()
    assert all(marker not in text for marker in ("private-object-marker", "private-query-marker", "private-token-marker"))


def test_lifecycle_reuses_provider_then_flushes_and_shuts_down(monkeypatch):
    """一个应用生命周期内复用Provider，退出时清理一次。"""
    exporter = InMemorySpanExporter()
    app = create_app(span_exporter=exporter)
    calls = []
    with TestClient(app) as client:
        provider = app.state.tracer_provider
        original_flush, original_shutdown = provider.force_flush, provider.shutdown

        def flush(*args, **kwargs):
            """追踪真实刷新调用。"""
            calls.append("flush")
            return original_flush(*args, **kwargs)

        def shutdown():
            """追踪真实关闭调用。"""
            calls.append("shutdown")
            original_shutdown()

        monkeypatch.setattr(provider, "force_flush", flush)
        monkeypatch.setattr(provider, "shutdown", shutdown)
        one, two = client.get("/health"), client.get("/health")
        assert app.state.tracer_provider is provider
        assert one.headers["X-Trace-ID"] != two.headers["X-Trace-ID"]
    assert calls == ["flush", "shutdown"] and app.state.tracer_provider is None
    assert len(exporter.get_finished_spans()) == 2


def test_applications_have_independent_providers():
    """反复创建测试应用不覆盖全局配置，也不向另一个导出器发送数据。"""
    first_exporter, second_exporter = InMemorySpanExporter(), InMemorySpanExporter()
    first, second = create_app(span_exporter=first_exporter), create_app(span_exporter=second_exporter)
    with TestClient(first) as one, TestClient(second) as two:
        assert first.state.tracer_provider is not second.state.tracer_provider
        assert one.get("/health").status_code == two.get("/health").status_code == 200
    assert len(first_exporter.get_finished_spans()) == len(second_exporter.get_finished_spans()) == 1


def test_shutdown_still_runs_if_flush_raises(monkeypatch):
    """刷新异常不跳过关闭与app.state清理。"""
    app = create_app(span_exporter=InMemorySpanExporter())
    calls = []
    with pytest.raises(RuntimeError, match="测试刷新失败"):
        with TestClient(app):
            provider = app.state.tracer_provider
            original_shutdown = provider.shutdown

            def broken_flush(*args, **kwargs):
                """模拟生命周期刷新故障。"""
                raise RuntimeError("测试刷新失败")

            def shutdown():
                """确认真实关闭仍执行。"""
                calls.append("shutdown")
                original_shutdown()

            monkeypatch.setattr(provider, "force_flush", broken_flush)
            monkeypatch.setattr(provider, "shutdown", shutdown)
    assert calls == ["shutdown"] and app.state.tracer_provider is None


def test_cancelled_http_task_ends_span_and_restores_context():
    """取消不吞异常、不添加原异常事件，Span正常结束且上下文恢复。"""
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(exporter=exporter)

    async def scenario():
        started = asyncio.Event()
        restored = []

        async def app(scope, receive, send):
            assert get_current_trace_id() is not None
            started.set()
            await asyncio.Event().wait()

        async def noop(*args):
            return None

        async def request():
            try:
                await RequestContextMiddleware(app)({"type": "http", "method": "GET", "app": SimpleNamespace(state=SimpleNamespace(tracer_provider=provider))}, noop, noop)
            finally:
                restored.append((get_current_trace_id(), get_request_id()))

        task = asyncio.create_task(request())
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert restored == [(None, None)]

    try:
        asyncio.run(scenario())
        saved, = exporter.get_finished_spans()
        assert saved.status.description == "cancelled" and not saved.events
    finally:
        provider.shutdown()


def test_exception_after_response_started_keeps_original_status_and_marks_error():
    """响应开始后不能改状态码，但仍可标记请求Span失败并结束。"""
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(exporter=exporter)

    async def scenario():
        messages = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"partial", "more_body": True})
            raise RuntimeError("private-stream-marker")

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        with pytest.raises(RuntimeError):
            await RequestContextMiddleware(app)({"type": "http", "method": "GET", "app": SimpleNamespace(state=SimpleNamespace(tracer_provider=provider))}, receive, send)
        assert messages[0]["status"] == 200 and messages[1]["body"] == b"partial"

    try:
        asyncio.run(scenario())
        saved, = exporter.get_finished_spans()
        assert saved.attributes["http.response.status_code"] == 200
        assert saved.status.status_code == StatusCode.ERROR and "private-stream-marker" not in saved.to_json()
    finally:
        provider.shutdown()
