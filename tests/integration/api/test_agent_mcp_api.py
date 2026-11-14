"""
文件职责：验证认证聊天实际经生产MCP Client和独立stdio Server查询工单。
作用与配合：真实JWT、文件测试库、完整应用；只有对话模型和向量模型使用替身。
边界：不查询真实工单或调用Ollama；不证明实际模型选工具的准确率。
"""

import json
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.factories.identity import build_user
from ticketpilot.agent import dispatcher
from ticketpilot.agent.dispatcher import UnknownToolError
from ticketpilot.agent.dependencies import get_agent_model
from ticketpilot.agent.model import FakeAgentModel
from ticketpilot.agent.schemas import ToolCallDecision, ToolRequest, FinalAnswerDecision
from ticketpilot.app import create_app
from ticketpilot.core.config import settings
from ticketpilot.core.database import Base, get_session
from ticketpilot.identity.models import UserRecord
from ticketpilot.identity.security import create_access_token
from ticketpilot.integrations import mcp_client
from ticketpilot.knowledge.api.dependencies import get_embedding_provider
from ticketpilot.knowledge.embeddings.base import EmbeddingProvider
from ticketpilot.tickets.models import TicketRecord
from ticketpilot.agent.providers.ollama import OllamaModelError


pytestmark = pytest.mark.integration


class AgentMcpContext(SimpleNamespace):
    """测试失败输出不包含测试JWT或配置。"""
    def __repr__(self):
        return "AgentMcpContext(isolated_database=True)"


@pytest.fixture
def agent_mcp_api(tmp_path, monkeypatch):
    """独立文件库让HTTP父进程与MCP子进程读取同一批测试数据。"""
    url = "sqlite:///" + (tmp_path / "business.sqlite").as_posix()
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        user = build_user(role="agent")
        ticket = TicketRecord(id=str(uuid4()), customer_name="测试客户", contact_email="private@example.com",
            channel="web", subject="真实MCP查询", description="协议链路", priority="medium", status="open")
        session.add_all([user, ticket])
        session.commit()
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr("agent-mcp-test-only-" + uuid4().hex))
    monkeypatch.setattr(settings, "enable_evidence_verification", False)
    model = FakeAgentModel([
        ToolCallDecision(kind="tool_call", tool_request=ToolRequest(name="get_ticket_details", arguments={"ticket_id": ticket.id})),
        FinalAnswerDecision(kind="final_answer", answer="工单当前状态为open。"),
    ])
    exporter = InMemorySpanExporter()
    app = create_app(span_exporter=exporter)
    def request_session():
        """每个HTTP请求重新创建业务Session。"""
        with factory() as current:
            yield current
    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[get_agent_model] = lambda: model
    app.dependency_overrides[get_embedding_provider] = lambda: MagicMock(spec=EmbeddingProvider)
    def forbidden_local_query(*args, **kwargs):
        """父进程如绕回原直接查询，测试立即失败。"""
        raise AssertionError("正式聊天不能绕过MCP直接查询")
    monkeypatch.setattr(dispatcher.ticket_tools, "get_ticket_details", forbidden_local_query)
    try:
        with TestClient(app) as client:
            yield AgentMcpContext(client=client, user=user, ticket=ticket, factory=factory, model=model, exporter=exporter)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def send(api):
    """使用真实签名令牌发送查询消息，令牌不出现在模型参数里。"""
    return api.client.post("/v1/agent/chat", headers={"Authorization": f"Bearer {create_access_token(api.user.id)}"},
                           json={"message": f"查询工单 {api.ticket.id}", "mode": "auto"})


def test_authenticated_chat_uses_real_stdio_mcp(agent_mcp_api):
    """Agent→Client→独立Server→数据库→模型，整条查询链路真实执行。"""
    api = agent_mcp_api
    response = send(api)
    assert response.status_code == 200 and response.json()["sources"] == []
    tool_message = next(message for message in api.model.received_messages[1] if message.role == "tool")
    data = json.loads(tool_message.content)
    assert data["ticket"]["id"] == api.ticket.id and data["ticket"]["subject"] == api.ticket.subject
    assert data["ticket"]["status"] == "open" and "private@example.com" not in tool_message.content
    with api.factory() as session:
        assert session.get(TicketRecord, api.ticket.id).status == "open"


def test_mcp_failure_returns_503_without_local_fallback(agent_mcp_api, monkeypatch):
    """依赖失败时不绕过Server身份检查或向模型提供伪造结果。"""
    api = agent_mcp_api
    async def unavailable(*args, **kwargs):
        """模拟生产Client错误。"""
        raise mcp_client.McpTicketError("internal-client-marker")
    monkeypatch.setattr(mcp_client, "query_ticket_via_mcp", unavailable)
    response = send(api)
    assert response.status_code == 503
    assert response.json() == {"detail": "MCP工单查询服务暂时不可用"}
    assert len(api.model.received_messages) == 1


def test_mcp_rechecks_identity_after_http_authentication(agent_mcp_api, monkeypatch):
    """HTTP认证后用户被停用，Server重新读取账号并拒绝，父进程不能代替授权。"""
    api = agent_mcp_api
    original = dispatcher.create_access_token
    def deactivate_then_issue(user_id):
        """在HTTP鉴权后、启动Server前改变账号状态。"""
        with api.factory() as session:
            session.get(UserRecord, user_id).is_active = False
            session.commit()
        return original(user_id)
    monkeypatch.setattr(dispatcher, "create_access_token", deactivate_then_issue)
    assert send(api).status_code == 503
    assert len(api.model.received_messages) == 1


def test_invalid_model_uuid_is_rejected_before_mcp(agent_mcp_api, monkeypatch):
    """新增协议要求UUID，非法模型参数返回422且不启动Client。"""
    api = agent_mcp_api
    api.model._remaining_decisions[0].tool_request.arguments["ticket_id"] = "bad-id"
    def should_not_start(*args, **kwargs):
        """非法参数不应到达传输层。"""
        raise AssertionError("非法UUID不应启动MCP")
    monkeypatch.setattr(dispatcher, "query_ticket_via_mcp_sync", should_not_start)
    assert send(api).status_code == 422


def test_real_stdio_call_exports_agent_model_tool_client_tree(agent_mcp_api):
    """真实stdio子进程调用与完整HTTP，验证六个父进程Span及耗时嵌套。"""
    api = agent_mcp_api
    response = send(api)
    assert response.status_code == 200
    spans = api.exporter.get_finished_spans()
    assert len(spans) == 6
    root, = [span for span in spans if span.kind == SpanKind.SERVER]
    agent, = [span for span in spans if span.name == "agent.run"]
    tool, = [span for span in spans if span.name == "tool.execute"]
    client, = [span for span in spans if span.name == "mcp.client"]
    models = [span for span in spans if span.name == "model.decide"]
    assert len(models) == 2 and client.kind == SpanKind.CLIENT
    assert root.parent is None and agent.parent.span_id == root.context.span_id
    assert tool.parent.span_id == agent.context.span_id and client.parent.span_id == tool.context.span_id
    assert all(span.parent.span_id == agent.context.span_id for span in models)
    assert all(format(span.context.trace_id, "032x") == response.headers["X-Trace-ID"] for span in spans)
    by_id = {span.context.span_id: span for span in spans}
    for span in spans:
        if span.parent:
            parent = by_id[span.parent.span_id]
            assert parent.start_time <= span.start_time <= span.end_time <= parent.end_time
        assert not span.events
        assert "private@example.com" not in span.to_json()
        assert api.ticket.id not in span.to_json()


def test_model_failure_ends_agent_and_model_without_running_tools(agent_mcp_api, monkeypatch):
    """模型依赖失败保持503，当前步骤和Agent都标失败且不泄露正文。"""
    api = agent_mcp_api

    def fail_model(*args, **kwargs):
        """模拟模型服务异常。"""
        raise OllamaModelError("private-model-marker")

    monkeypatch.setattr(api.model, "decide", fail_model)
    response = send(api)
    assert response.status_code == 503
    spans = api.exporter.get_finished_spans()
    assert {span.name for span in spans if span.kind != SpanKind.SERVER} == {"agent.run", "model.decide"}
    assert all(span.status.status_code == StatusCode.ERROR for span in spans)
    assert all(not span.events and "private-model-marker" not in span.to_json() for span in spans)


def test_client_failure_propagates_through_client_tool_and_agent_spans(agent_mcp_api, monkeypatch):
    """生产Client包装仍执行，依赖故障不触发本地回退，各父级步骤保留失败。"""
    api = agent_mcp_api

    async def fail_transport(*args, **kwargs):
        """替换传输实现，不绕过公共Client的Span包装。"""
        raise mcp_client.McpTicketError("private-transport-marker")

    monkeypatch.setattr(mcp_client, "_query_ticket_via_mcp", fail_transport)
    response = send(api)
    assert response.status_code == 503
    spans = api.exporter.get_finished_spans()
    for name in ("mcp.client", "tool.execute", "agent.run"):
        span, = [span for span in spans if span.name == name]
        assert span.status.status_code == StatusCode.ERROR
    assert len([span for span in spans if span.name == "model.decide"]) == 1
    assert all(not span.events and "private-transport-marker" not in span.to_json() for span in spans)


def test_invalid_uuid_records_tool_failure_without_client_span(agent_mcp_api):
    """参数验证失败发生在传输前，Trace清楚区分未启动Client。"""
    api = agent_mcp_api
    api.model._remaining_decisions[0].tool_request.arguments["ticket_id"] = "invalid-id"
    assert send(api).status_code == 422
    spans = api.exporter.get_finished_spans()
    assert not any(span.name == "mcp.client" for span in spans)
    tool, = [span for span in spans if span.name == "tool.execute"]
    assert tool.status.status_code == StatusCode.ERROR
    root, = [span for span in spans if span.kind == SpanKind.SERVER]
    assert root.status.status_code == StatusCode.UNSET


def test_unknown_tool_name_is_not_exported_or_printed_raw(agent_mcp_api, capsys):
    """未知名称只记录unknown，原始字符串不进入Span或旧耗时输出。"""
    api = agent_mcp_api
    marker = "private-unknown-tool-marker"
    api.model._remaining_decisions[0].tool_request.name = marker
    with pytest.raises(UnknownToolError):
        send(api)
    spans = api.exporter.get_finished_spans()
    tool, = [span for span in spans if span.name == "tool.execute"]
    assert tool.attributes["ticketpilot.tool.name"] == "unknown"
    assert all(marker not in span.to_json() for span in spans)
    assert marker not in capsys.readouterr().out
