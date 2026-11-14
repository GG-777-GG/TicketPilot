"""
文件职责：通过真实MCP Client验证只读Server的发现、调用和身份边界。
作用与配合：使用内存协议及stdio子进程，数据库和令牌均为测试专用。
边界：不访问真实工单，不连接Ollama，不宣称Agent已切换到MCP。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from mcp import Client, StdioServerParameters
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from tests.factories.identity import build_user
from ticketpilot.integrations import mcp_server
from ticketpilot.core.config import settings
from ticketpilot.core.database import Base
from ticketpilot.identity.security import create_access_token
from ticketpilot.tickets.models import TicketRecord


pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class McpTestContext(SimpleNamespace):
    """上下文诊断不显示测试签名密钥或JWT。"""
    def __repr__(self):
        return "McpTestContext(isolated_database=True)"


@pytest.fixture
def mcp_case(database_session, monkeypatch):
    """内存协议使用正式服务对象，只替换Session工厂和测试环境身份。"""
    user = build_user(role="agent")
    ticket = TicketRecord(
        id=str(uuid4()), customer_name="不应公开的姓名", contact_email="private@example.com",
        channel="web", subject="查询测试", description="测试问题", priority="medium", status="open",
    )
    database_session.add_all([user, ticket])
    database_session.commit()
    factory = sessionmaker(bind=database_session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "SessionLocal", factory)
    monkeypatch.setenv("TICKETPILOT_MCP_TOKEN", create_access_token(user.id))
    return McpTestContext(session=database_session, user=user, ticket=ticket)


def query(arguments, *, name="get_ticket_details"):
    """走Client和协议分发，不直接调用Python业务函数。"""
    async def scenario():
        async with Client(mcp_server.server) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(asyncio.wait_for(scenario(), timeout=10))


def snapshot(case):
    """核对全部工单列，确认查询不会修改数据。"""
    return [dict(row) for row in case.session.execute(select(TicketRecord.__table__)).mappings()]


def test_discovery_only_exposes_read_tool(mcp_case):
    """工具描述公开参数只有ticket_id，没有审核、执行或身份参数。"""
    async def scenario():
        async with Client(mcp_server.server) as client:
            return await client.list_tools()
    result = asyncio.run(scenario())
    assert [tool.name for tool in result.tools] == ["get_ticket_details"]
    tool = result.tools[0]
    assert tool.input_schema["required"] == ["ticket_id"]
    assert set(tool.input_schema["properties"]) == {"ticket_id"}
    assert tool.input_schema["properties"]["ticket_id"]["format"] == "uuid"
    assert tool.output_schema


@pytest.mark.parametrize("role", ["agent", "supervisor", "admin"])
def test_protocol_query_returns_public_data_without_writing(mcp_case, role):
    """三种有效角色查询真实测试工单，仅返回原工具允许的公开字段。"""
    case = mcp_case
    case.user.role = role
    case.session.commit()
    before = snapshot(case)
    result = query({"ticket_id": case.ticket.id})
    assert not result.is_error
    body = result.structured_content
    assert body["found"] is True and body["ticket"]["id"] == case.ticket.id
    assert body["ticket"]["status"] == "open"
    assert set(body["ticket"]) == {"id", "subject", "description", "priority", "status", "assigned_to"}
    assert "private@example.com" not in str(body) and "password_hash" not in str(body)
    assert snapshot(case) == before


def test_protocol_reports_missing_ticket(mcp_case):
    """不存在的工单是正常查询结果，区别于协议或认证失败。"""
    result = query({"ticket_id": str(uuid4())})
    assert not result.is_error
    assert result.structured_content["found"] is False
    assert result.structured_content["ticket"] is None


@pytest.mark.parametrize("arguments", [{}, {"ticket_id": "bad-id"}, {"ticket_id": None}])
def test_protocol_rejects_invalid_parameters(mcp_case, arguments):
    """UUID和必填参数校验通过实际MCP调用验证。"""
    before = snapshot(mcp_case)
    result = query(arguments)
    assert result.is_error and snapshot(mcp_case) == before


@pytest.mark.parametrize("kind", ["missing", "invalid", "expired", "unknown_user", "disabled", "unknown_role"])
def test_protocol_enforces_current_identity(mcp_case, monkeypatch, kind):
    """身份不合法时返回工具错误，模型参数中的角色不能改变授权。"""
    case = mcp_case
    if kind == "missing":
        monkeypatch.delenv("TICKETPILOT_MCP_TOKEN")
    elif kind == "invalid":
        monkeypatch.setenv("TICKETPILOT_MCP_TOKEN", "invalid-token")
    elif kind == "expired":
        now = datetime.now(UTC)
        token = jwt.encode({"sub": case.user.id, "iat": now - timedelta(minutes=5), "exp": now - timedelta(minutes=1)},
                           settings.jwt_secret_key.get_secret_value(), algorithm="HS256")
        monkeypatch.setenv("TICKETPILOT_MCP_TOKEN", token)
    elif kind == "unknown_user":
        monkeypatch.setenv("TICKETPILOT_MCP_TOKEN", create_access_token(str(uuid4())))
    elif kind == "disabled":
        case.user.is_active = False
    else:
        case.user.role = "unknown"
    case.session.commit()
    before = snapshot(case)
    result = query({"ticket_id": case.ticket.id})
    assert result.is_error and snapshot(case) == before
    assert "password_hash" not in str(result.content)


def test_model_cannot_supply_identity_in_tool_arguments(mcp_case, monkeypatch):
    """没有可信访问令牌时，自报管理角色和用户编号也不能取得数据。"""
    case = mcp_case
    monkeypatch.delenv("TICKETPILOT_MCP_TOKEN")
    result = query({"ticket_id": case.ticket.id, "role": "admin", "user_id": case.user.id})
    assert result.is_error and case.ticket.subject not in str(result.content)


def test_same_client_rechecks_account_status(mcp_case):
    """连接存在不等于永久授权，每次调用都重新检查账号。"""
    case = mcp_case
    async def scenario():
        async with Client(mcp_server.server) as client:
            first = await client.call_tool("get_ticket_details", {"ticket_id": case.ticket.id})
            case.user.is_active = False
            case.session.commit()
            second = await client.call_tool("get_ticket_details", {"ticket_id": case.ticket.id})
            return first, second
    first, second = asyncio.run(scenario())
    assert not first.is_error and second.is_error


@pytest.mark.parametrize("target", ["user", "ticket"])
def test_storage_errors_are_masked(mcp_case, monkeypatch, target):
    """内部存储故障返回固定工具错误，不向Client返回底层消息。"""
    def fail(*args, **kwargs):
        """模拟内部异常。"""
        raise RuntimeError("internal-database-marker")
    module, name = (mcp_server.user_repository, "get_user_by_id") if target == "user" else (mcp_server.ticket_tools, "get_ticket_details")
    monkeypatch.setattr(module, name, fail)
    result = query({"ticket_id": mcp_case.ticket.id})
    assert result.is_error
    assert "工单查询服务暂时不可用" in str(result.content)
    assert "internal-database-marker" not in str(result.content)


def test_write_tool_is_not_available(mcp_case):
    """Server未注册关闭工具，Client无法调用它修改工单。"""
    before = snapshot(mcp_case)
    result = query({"ticket_id": mcp_case.ticket.id}, name="close_ticket")
    assert result.is_error and snapshot(mcp_case) == before


def test_stdio_process_discovers_and_queries_isolated_database(tmp_path, monkeypatch):
    """Client真实启动独立Server进程，通过stdio查询临时文件数据库。"""
    import sys
    database_path = tmp_path / "business.sqlite"
    database_url = "sqlite:///" + database_path.as_posix()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        user = build_user(role="agent")
        ticket = TicketRecord(id=str(uuid4()), customer_name="测试客户", contact_email="test@example.com",
                              channel="web", subject="stdio查询", description="独立进程", priority="medium", status="open")
        session.add_all([user, ticket])
        session.commit()
        user_id, ticket_id = user.id, ticket.id
    secret = "mcp-test-only-" + uuid4().hex
    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr(secret))
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "ticketpilot.integrations.mcp_server"], cwd=PROJECT_ROOT,
        env={"DATABASE_URL": database_url, "JWT_SECRET_KEY": secret,
             "TICKETPILOT_MCP_TOKEN": create_access_token(user_id), "PYTHONIOENCODING": "utf-8"},
    )
    async def scenario():
        async with Client(parameters, read_timeout_seconds=5) as client:
            tools = await client.list_tools()
            result = await client.call_tool("get_ticket_details", {"ticket_id": ticket_id})
            return tools, result
    try:
        tools, result = asyncio.run(asyncio.wait_for(scenario(), timeout=15))
        assert [tool.name for tool in tools.tools] == ["get_ticket_details"]
        assert not result.is_error and result.structured_content["ticket"]["id"] == ticket_id
        with factory() as session:
            assert session.get(TicketRecord, ticket_id).status == "open"
    finally:
        engine.dispose()
