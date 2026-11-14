"""
文件职责：验证请求上下文的嵌套恢复、异常、取消、异步隔离和工作线程传播。
作用与配合：真实ContextVar及ASGI中间件，不连接数据库或网络。
边界：不声明独立MCP进程传播或OpenTelemetry已接入。
"""

import asyncio
from uuid import UUID, uuid4

import anyio
import pytest

from ticketpilot.observability.context import get_request_id, request_context
from ticketpilot.observability.middleware import RequestContextMiddleware


def test_outside_request_has_no_identifier():
    """普通脚本没有请求上下文时不伪造编号。"""
    assert get_request_id() is None


def test_generated_identifier_is_restored_after_scope():
    """自动生成UUID，作用域结束后恢复为空。"""
    with request_context() as identifier:
        assert isinstance(identifier, UUID) and get_request_id() == identifier
    assert get_request_id() is None


def test_trusted_identifier_is_used():
    """可信后端可以复用指定UUID。"""
    identifier = uuid4()
    with request_context(identifier):
        assert get_request_id() == identifier
    assert get_request_id() is None


def test_nested_scope_restores_outer_identifier():
    """内层退出不能清空外层已有编号。"""
    with request_context() as outer:
        with request_context() as inner:
            assert inner != outer and get_request_id() == inner
        assert get_request_id() == outer
    assert get_request_id() is None


def test_exception_restores_original_context():
    """异常继续传播，同时finally恢复原上下文。"""
    original = RuntimeError("测试故障")
    with request_context() as outer:
        with pytest.raises(RuntimeError) as captured:
            with request_context():
                raise original
        assert captured.value is original and get_request_id() == outer


@pytest.mark.parametrize("value", ["untrusted-string", 42])
def test_invalid_identifier_is_rejected_without_changing_context(value):
    """错误参数不污染原上下文。"""
    with request_context() as outer:
        with pytest.raises(TypeError):
            with request_context(value):
                raise AssertionError("不应进入作用域")
        assert get_request_id() == outer


def test_overlapping_async_tasks_keep_their_own_identifiers():
    """用事件保证两个作用域重叠，不靠延时猜测并发是否发生。"""
    async def scenario():
        ready = [asyncio.Event(), asyncio.Event()]

        async def worker(index):
            with request_context() as identifier:
                ready[index].set()
                await asyncio.wait_for(ready[1 - index].wait(), timeout=5)
                assert get_request_id() == identifier
                return identifier

        identifiers = await asyncio.gather(worker(0), worker(1))
        assert identifiers[0] != identifiers[1] and get_request_id() is None

    asyncio.run(scenario())


def test_cancellation_still_resets_context():
    """取消任务会经过finally，不吞掉取消异常。"""
    async def scenario():
        started = asyncio.Event()
        restored = []

        async def worker():
            try:
                with request_context():
                    started.set()
                    await asyncio.Event().wait()
            finally:
                restored.append(get_request_id())

        task = asyncio.create_task(worker())
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert restored == [None] and get_request_id() is None

    asyncio.run(scenario())


def test_anyio_worker_receives_context_without_mutating_parent():
    """同步工作线程取得调用方编号，其内层修改不会污染调用方。"""
    async def scenario():
        with request_context() as identifier:
            def worker():
                """模拟同步路由及其内层上下文。"""
                assert get_request_id() == identifier
                with request_context() as inner:
                    assert inner != identifier
                return get_request_id()

            assert await anyio.to_thread.run_sync(worker) == identifier
            assert get_request_id() == identifier

    anyio.run(scenario)


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
def test_non_http_scope_passes_through_without_request_context(scope_type):
    """非HTTP消息不被改写或强加HTTP编号。"""
    async def scenario():
        calls = []

        async def app(scope, receive, send):
            calls.append(scope["type"])
            assert get_request_id() is None

        async def noop(*args):
            return None

        await RequestContextMiddleware(app)({"type": scope_type}, noop, noop)
        assert calls == [scope_type]

    asyncio.run(scenario())


def test_stream_messages_are_preserved_and_header_overridden():
    """只添加服务器编号，正文分块不改变，结束后恢复外层上下文。"""
    async def scenario():
        messages = []

        async def app(scope, receive, send):
            assert get_request_id() == scope["state"]["request_id"]
            await send({"type": "http.response.start", "status": 200, "headers": [(b"x-request-id", b"downstream")]})
            await send({"type": "http.response.body", "body": b"part1", "more_body": True})
            await send({"type": "http.response.body", "body": b"part2", "more_body": False})

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        with request_context() as outer:
            scope = {"type": "http", "state": {}}
            await RequestContextMiddleware(app)(scope, receive, send)
            assert get_request_id() == outer
            assert dict(messages[0]["headers"])[b"x-request-id"].decode() == str(scope["state"]["request_id"])
            assert scope["state"]["request_id"] != outer
        assert [item["body"] for item in messages[1:]] == [b"part1", b"part2"]

    asyncio.run(scenario())


def test_middleware_exception_preserves_error_and_resets_context():
    """原始异常交给外层处理，同时请求上下文已恢复。"""
    async def scenario():
        failure = RuntimeError("测试未捕获异常")

        async def app(scope, receive, send):
            assert get_request_id() is not None
            raise failure

        async def noop(*args):
            return None

        with request_context() as outer:
            with pytest.raises(RuntimeError) as captured:
                await RequestContextMiddleware(app)({"type": "http"}, noop, noop)
            assert captured.value is failure and get_request_id() == outer

    asyncio.run(scenario())
