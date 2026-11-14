"""
文件职责：验证MCP Client的数据校验、超时、取消、清理和同步桥接。
作用与配合：使用可控Client替身，验证适配器边界，不启动真实子进程。
边界：不访问数据库、网络或业务令牌；stdio完整链路由集成测试验证。
"""

import asyncio
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from ticketpilot.integrations import mcp_client
from ticketpilot.core.config import BASE_DIR, settings


@pytest.fixture
def client_case(monkeypatch):
    """使用测试配置与可控资源生命周期，异常时不包含真实密钥。"""
    ticket_id = uuid4()
    body = {"found": True, "message": "已找到", "ticket": {
        "id": str(ticket_id), "subject": "测试", "description": "问题", "priority": "medium",
        "status": "open", "assigned_to": None,
    }}
    case = SimpleNamespace(ticket_id=ticket_id, body=body, failure=None, is_error=False,
                           tool_names=["get_ticket_details"], opened=False, closed=False,
                           parameters=None, kwargs=None, called=None, delay=False)
    monkeypatch.setattr(settings, "database_url", "sqlite:///unit-test-only.sqlite")
    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr("unit-test-only-" + uuid4().hex))

    class FakeClient:
        """模拟SDK资源对象，记录进入、退出与工具请求。"""
        def __init__(self, parameters, **kwargs):
            case.parameters, case.kwargs = parameters, kwargs

        async def __aenter__(self):
            if case.failure == "enter":
                raise OSError("internal-marker")
            case.opened = True
            return self

        async def __aexit__(self, *args):
            case.closed = True

        async def list_tools(self):
            if case.failure == "list":
                raise OSError("internal-marker")
            return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in case.tool_names])

        async def call_tool(self, name, arguments):
            case.called = (name, arguments)
            if case.delay:
                await asyncio.sleep(10)
            if case.failure == "call":
                raise OSError("internal-marker")
            if case.failure == "timeout":
                raise TimeoutError("internal-marker")
            return SimpleNamespace(is_error=case.is_error, structured_content=case.body)

    monkeypatch.setattr(mcp_client, "Client", FakeClient)
    return case


def query(case, token="test-token"):
    """运行异步查询入口，避免引入额外pytest异步插件。"""
    return asyncio.run(mcp_client.query_ticket_via_mcp(case.ticket_id, access_token=token))


def test_valid_result_and_fixed_launch_configuration(client_case):
    """查询固定本地模块并通过环境传递身份，结果转换为业务模型。"""
    case = client_case
    result = query(case)
    assert result.ticket.id == str(case.ticket_id) and case.opened and case.closed
    assert case.parameters.command == sys.executable
    assert case.parameters.args == ["-m", "ticketpilot.integrations.mcp_server"]
    assert case.parameters.cwd == BASE_DIR
    assert case.parameters.env["TICKETPILOT_MCP_TOKEN"] == "test-token"
    assert case.kwargs["read_timeout_seconds"] == 5
    assert case.called == ("get_ticket_details", {"ticket_id": str(case.ticket_id)})


def test_not_found_is_normal_result(client_case):
    """合法的未找到结果不能被误报成服务故障。"""
    client_case.body = {"found": False, "message": "不存在", "ticket": None}
    assert query(client_case).found is False and client_case.closed


def test_missing_token_does_not_launch_client(client_case):
    """缺少身份时拒绝，不生成子进程连接参数。"""
    with pytest.raises(mcp_client.McpTicketError, match="缺少"):
        query(client_case, "")
    assert client_case.parameters is None


def test_missing_tool_closes_connection(client_case):
    """工具发现缺少白名单目标时停止，不调用其他工具。"""
    client_case.tool_names = ["unrelated_tool"]
    with pytest.raises(mcp_client.McpTicketError, match="未提供"):
        query(client_case)
    assert client_case.called is None and client_case.closed


@pytest.mark.parametrize("failure", ["enter", "list", "call"])
def test_transport_errors_are_masked(client_case, failure):
    """连接与通信错误不公开底层内容，已进入的连接会退出。"""
    client_case.failure = failure
    with pytest.raises(mcp_client.McpTicketError) as caught:
        query(client_case)
    assert "internal-marker" not in str(caught.value)
    assert caught.value.__suppress_context__
    if failure != "enter":
        assert client_case.closed


def test_tool_error_is_rejected(client_case):
    """收到协议响应但工具执行失败，不能作为正常数据返回。"""
    client_case.is_error = True
    with pytest.raises(mcp_client.McpTicketError, match="查询失败"):
        query(client_case)
    assert client_case.closed


@pytest.mark.parametrize("kind", ["missing_structure", "bad_schema", "wrong_ticket", "found_without_ticket", "ticket_without_found"])
def test_invalid_results_are_rejected(client_case, kind):
    """拒绝非法结构、不同目标和found/ticket矛盾。"""
    case = client_case
    if kind == "missing_structure":
        case.body = None
    elif kind == "bad_schema":
        case.body = {"found": "not-a-bool"}
    elif kind == "wrong_ticket":
        case.body["ticket"]["id"] = str(uuid4())
    elif kind == "found_without_ticket":
        case.body["ticket"] = None
    else:
        case.body["found"] = False
    with pytest.raises(mcp_client.McpTicketError):
        query(case)
    assert case.closed


def test_read_timeout_is_mapped(client_case):
    """SDK读取超时映射稳定异常，并退出连接。"""
    client_case.failure = "timeout"
    with pytest.raises(mcp_client.McpTicketError, match="超时"):
        query(client_case)
    assert client_case.closed


def test_overall_timeout_cancels_and_closes(client_case, monkeypatch):
    """实际触发整体超时，验证等待被取消而连接仍清理。"""
    original_timeout = asyncio.timeout
    monkeypatch.setattr(mcp_client.asyncio, "timeout", lambda seconds: original_timeout(0.02))
    client_case.delay = True
    with pytest.raises(mcp_client.McpTicketError, match="超时"):
        query(client_case)
    assert client_case.closed


def test_cancellation_is_not_swallowed(client_case):
    """调用方取消要继续传播，不伪装为普通工具结果。"""
    client_case.delay = True
    async def scenario():
        task = asyncio.create_task(mcp_client.query_ticket_via_mcp(client_case.ticket_id, access_token="test-token"))
        while client_case.called is None:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert client_case.closed


def test_sync_bridge_runs_in_sync_thread(client_case):
    """同步入口取得结果，生命周期仍由异步Client管理。"""
    result = mcp_client.query_ticket_via_mcp_sync(client_case.ticket_id, access_token="test-token")
    assert result.ticket.id == str(client_case.ticket_id) and client_case.closed


def test_sync_bridge_rejects_existing_event_loop(client_case):
    """已有事件循环时明确拒绝同步桥，且不创建未await的协程对象。"""
    async def scenario():
        with pytest.raises(mcp_client.McpTicketError, match="事件循环"):
            mcp_client.query_ticket_via_mcp_sync(client_case.ticket_id, access_token="test-token")
    asyncio.run(scenario())
    assert client_case.parameters is None
