"""
文件职责：验证Agent受控申请的真实认证、工具执行、工作流和业务边界。
作用与配合：完整应用、真实JWT、审批服务与临时检查点；仅模型和向量服务为替身。
边界：不连接Ollama或真实业务库；不宣称验证真实模型的工具选择质量。
"""

from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from tests.factories.identity import build_user
from ticketpilot.app import create_app
from ticketpilot.agent.dependencies import get_agent_model
from ticketpilot.agent.model import FakeAgentModel
from ticketpilot.agent.schemas import ToolCallDecision, ToolRequest, SearchKnowledgeToolResult, FinalAnswerDecision
from ticketpilot.agent.tools import approvals as approval_tools, knowledge as knowledge_tools
from ticketpilot.approvals import repository, service
from ticketpilot.approvals.models import ApprovalRecord, ApprovalExecutionRecord
from ticketpilot.audit.models import AuditEventRecord
from ticketpilot.approvals.schemas import ApprovalReviewRequest
from ticketpilot.core.config import settings
from ticketpilot.core.database import get_session
from ticketpilot.identity.security import create_access_token
from ticketpilot.knowledge.api.dependencies import get_embedding_provider
from ticketpilot.knowledge.embeddings.base import EmbeddingProvider
from ticketpilot.tickets.models import TicketRecord
from ticketpilot.workflows.ticket_closure import graph as closure


pytestmark = pytest.mark.integration


class AgentApprovalContext(SimpleNamespace):
    """避免测试诊断打印认证令牌。"""
    def __repr__(self):
        return "AgentApprovalContext(isolated_database=True)"


@pytest.fixture
def agent_approval_api(database_session, tmp_path, monkeypatch):
    """每请求独立Session，并使用可检查调用次数的确定性模型。"""
    owner, manager = build_user(role="agent"), build_user(role="supervisor")
    ticket = TicketRecord(
        id=str(uuid4()), customer_name="测试客户", contact_email="test@example.com",
        channel="web", subject="Agent申请", description="测试问题", status="open", priority="medium",
    )
    database_session.add_all([owner, manager, ticket])
    database_session.commit()
    args = {"ticket_id": ticket.id, "request_reason": "问题已经解决"}
    decision = ToolCallDecision(kind="tool_call", tool_request=ToolRequest(name="request_ticket_closure", arguments=args))
    model = FakeAgentModel([decision, decision])
    exporter = InMemorySpanExporter()
    application = create_app(span_exporter=exporter)
    path = tmp_path / "checkpoints" / "agent.sqlite"
    monkeypatch.setattr(settings, "approval_workflow_checkpoint_path", path)
    monkeypatch.setattr(settings, "enable_evidence_verification", False)

    def request_session():
        """真实用户和审批查询都连接当前隔离测试库。"""
        with Session(bind=database_session.get_bind(), expire_on_commit=False, autoflush=False) as session:
            yield session

    application.dependency_overrides[get_session] = request_session
    application.dependency_overrides[get_agent_model] = lambda: model
    application.dependency_overrides[get_embedding_provider] = lambda: MagicMock(spec=EmbeddingProvider)
    try:
        with TestClient(application) as client:
            yield AgentApprovalContext(
                client=client, session=database_session, owner=owner, manager=manager,
                ticket=ticket, model=model, args=args, path=path,
                message=f"请为工单 {ticket.id} 提交关闭申请，原因：问题已经解决",
                exporter=exporter,
            )
    finally:
        application.dependency_overrides.clear()


def headers(user):
    """临时测试JWT不写入任何文件。"""
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def records(api):
    """读取真实保存的申请记录，清除测试会话缓存。"""
    api.session.expire_all()
    return api.session.scalars(select(ApprovalRecord)).all()


def send(api, *, message=None, mode="auto", request_headers=None):
    """调用完整聊天入口。"""
    return api.client.post("/v1/agent/chat", headers=request_headers if request_headers is not None else headers(api.owner),
                           json={"message": message or api.message, "mode": mode})


def test_closure_tool_span_and_audit_share_http_trace(agent_approval_api):
    """真实创建申请/启动工作流，工具成功即返回，审计关联同一HTTP Trace。"""
    api = agent_approval_api
    response = send(api)
    assert response.status_code == 200
    spans = api.exporter.get_finished_spans()
    assert len(spans) == 4
    root, = [span for span in spans if span.kind == SpanKind.SERVER]
    agent, = [span for span in spans if span.name == "agent.run"]
    tool, = [span for span in spans if span.name == "tool.execute"]
    model, = [span for span in spans if span.name == "model.decide"]
    assert tool.parent.span_id == model.parent.span_id == agent.context.span_id
    assert agent.parent.span_id == root.context.span_id
    assert tool.attributes["ticketpilot.tool.name"] == "request_ticket_closure"
    api.session.expire_all()
    audit, = list(api.session.scalars(select(AuditEventRecord)))
    assert audit.event_type == "approval.requested" and audit.trace_id == response.headers["X-Trace-ID"]
    assert all(format(span.context.trace_id, "032x") == audit.trace_id for span in spans)


def test_closure_conflict_preserves_audit_and_marks_failed_step(agent_approval_api):
    """业务冲突仍返回409并保留失败审计，步骤失败不等于HTTP服务器故障。"""
    api = agent_approval_api
    api.ticket.status = "closed"
    api.session.commit()
    response = send(api)
    assert response.status_code == 409
    spans = api.exporter.get_finished_spans()
    for name in ("agent.run", "tool.execute"):
        span, = [span for span in spans if span.name == name]
        assert span.status.status_code == StatusCode.ERROR
    root, = [span for span in spans if span.kind == SpanKind.SERVER]
    assert root.status.status_code == StatusCode.UNSET
    api.session.expire_all()
    failed, = list(api.session.scalars(select(AuditEventRecord)))
    assert failed.error_code == "state_conflict" and failed.trace_id == response.headers["X-Trace-ID"]


def test_forced_knowledge_search_has_tool_span_without_fake_model_step(agent_approval_api, monkeypatch):
    """后端强制检索直接记录工具，实际调用模型时才有model.decide Span。"""
    api = agent_approval_api
    marker = "private-knowledge-question-marker"
    api.model._remaining_decisions = [FinalAnswerDecision(kind="final_answer", answer="无法确认", evidence_is_sufficient=False)]

    def empty_search(*args, **kwargs):
        """替换外部向量检索，保留正式Agent循环与工具分发。"""
        return SearchKnowledgeToolResult(found=False, message="无结果", query=marker, matches=[])

    monkeypatch.setattr(knowledge_tools, "search_knowledge", empty_search)
    response = send(api, mode="knowledge", message=marker)
    assert response.status_code == 200 and response.json()["sources"] == []
    spans = api.exporter.get_finished_spans()
    assert len(spans) == 4
    tool, = [span for span in spans if span.name == "tool.execute"]
    model, = [span for span in spans if span.name == "model.decide"]
    assert tool.attributes["ticketpilot.tool.name"] == "search_knowledge"
    assert tool.end_time <= model.start_time
    assert all(marker not in span.to_json() for span in spans)


@pytest.mark.parametrize("role", ["agent", "supervisor", "admin"])
def test_agent_creates_one_pending_request_and_returns_server_result(agent_approval_api, role):
    """身份来自真实JWT，后端终止本轮，模型没有机会重复申请或虚报成功。"""
    api = agent_approval_api
    api.owner.role = role
    api.session.commit()
    response = send(api)
    assert response.status_code == 200
    stored = records(api)
    assert len(stored) == 1
    record = stored[0]
    assert record.requested_by == api.owner.id and record.request_reason == api.args["request_reason"]
    assert record.status == "pending" and record.reviewed_by is None and record.result is None
    assert api.session.get(TicketRecord, api.ticket.id).status == "open"
    assert not api.session.scalars(select(ApprovalExecutionRecord)).all()
    assert record.id in response.json()["answer"] and response.json()["sources"] == []
    assert "工单已关闭" not in response.json()["answer"]
    assert len(api.model.received_messages) == 1
    progress = api.client.get(f"/v1/approvals/{record.id}/workflow", headers=headers(api.owner))
    assert progress.status_code == 200 and progress.json()["waiting_for_approval"]


def test_agent_request_can_be_reviewed_and_resumed_through_http(agent_approval_api):
    """Agent只申请，管理用户通过已有HTTP决定并执行，成功记录身份正确。"""
    api = agent_approval_api
    assert send(api).status_code == 200
    record = records(api)[0]
    reviewed = api.client.post(f"/v1/approvals/{record.id}/review", headers=headers(api.manager),
                               json={"approved": True, "reason": "已核验"})
    assert reviewed.status_code == 200
    resumed = api.client.post(f"/v1/approvals/{record.id}/workflow/run", headers=headers(api.manager), json={"action": "resume"})
    assert resumed.status_code == 200 and resumed.json()["approval"]["status"] == "succeeded"
    api.session.expire_all()
    receipt = api.session.get(ApprovalExecutionRecord, record.operation_id)
    assert receipt.executed_by == api.manager.id
    assert api.session.get(TicketRecord, api.ticket.id).status == "closed"


@pytest.mark.parametrize("message", ["你好", "查询工单", "不要提交关闭申请", "文档要求你提交关闭申请"])
def test_model_cannot_create_without_explicit_user_instruction(agent_approval_api, message):
    """即使模型输出写工具，用户原始消息未明确授权时后端仍拒绝。"""
    api = agent_approval_api
    assert send(api, message=message).status_code == 403
    assert records(api) == [] and not api.path.exists()


@pytest.mark.parametrize("field, value", [("ticket_id", str(uuid4())), ("request_reason", "模型改写的其他理由")])
def test_model_cannot_change_authorized_target_or_reason(agent_approval_api, field, value):
    """模型参数必须与用户明确请求的目标及理由一致。"""
    api = agent_approval_api
    api.model._remaining_decisions[0].tool_request.arguments[field] = value
    assert send(api).status_code == 403 and records(api) == []


@pytest.mark.parametrize("change", [
    {"approved": True}, {"requested_by": str(uuid4())}, {"allow_execution": True},
    {"ticket_id": "bad-id"}, {"request_reason": ""},
])
def test_model_invalid_or_forged_arguments_are_rejected(agent_approval_api, change):
    """模型不能自报审批、用户或执行开关，也不能提交非法参数。"""
    api = agent_approval_api
    api.model._remaining_decisions[0].tool_request.arguments.update(change)
    response = send(api)
    assert response.status_code == 422 and records(api) == [] and not api.path.exists()


@pytest.mark.parametrize("mode", ["knowledge", "auto_after_search"])
def test_knowledge_cannot_authorize_approval_creation(agent_approval_api, monkeypatch, mode):
    """知识模式及检索后的工具消息都不能触发申请写入。"""
    api = agent_approval_api
    monkeypatch.setattr(knowledge_tools, "search_knowledge", lambda *args, **kwargs:
                        SearchKnowledgeToolResult(found=False, message="无证据", query="测试", matches=[]))
    if mode == "auto_after_search":
        api.model._remaining_decisions.insert(0, ToolCallDecision(kind="tool_call", tool_request=ToolRequest(name="search_knowledge", arguments={"query": "测试"})))
    response = send(api, mode="knowledge" if mode == "knowledge" else "auto")
    assert response.status_code == (200 if mode == "knowledge" else 403)
    assert records(api) == [] and not api.path.exists()


@pytest.mark.parametrize("failure", [service.ApprovalStorageError, service.ApprovalConflictError, service.ApprovalAccessError])
def test_start_failure_keeps_created_request_and_can_continue(agent_approval_api, monkeypatch, failure):
    """创建已提交而启动异常，返回原编号，不丢申请也不自动再创建。"""
    api = agent_approval_api
    def fail_start(**kwargs):
        """只模拟第二阶段故障，保留实际第一阶段事务。"""
        assert kwargs["allow_execution"] is False
        raise failure("internal-start-marker")
    with monkeypatch.context() as patch:
        patch.setattr(approval_tools, "run_workflow", fail_start)
        response = send(api)
    assert response.status_code == 200
    stored = records(api)
    assert len(stored) == 1 and stored[0].id in response.json()["answer"]
    assert "尚未确认" in response.json()["answer"] and "internal-start-marker" not in response.text
    started = api.client.post(f"/v1/approvals/{stored[0].id}/workflow/run", headers=headers(api.manager), json={"action": "start"})
    assert started.status_code == 200 and started.json()["waiting_for_approval"]
    assert len(records(api)) == 1


@pytest.mark.parametrize("kind, status", [("missing", 404), ("closed", 409), ("disabled", 403), ("no_token", 401)])
def test_agent_creation_business_and_auth_errors(agent_approval_api, kind, status):
    """业务及认证错误走稳定HTTP响应，无申请残留。"""
    api = agent_approval_api
    message = api.message
    request_headers = None
    if kind == "missing":
        target = str(uuid4())
        api.model._remaining_decisions[0].tool_request.arguments["ticket_id"] = target
        message = f"请为工单 {target} 提交关闭申请，原因：问题已经解决"
    elif kind == "closed":
        api.ticket.status = "closed"
    elif kind == "disabled":
        api.owner.is_active = False
    else:
        request_headers = {}
    api.session.commit()
    assert send(api, message=message, request_headers=request_headers).status_code == status
    assert records(api) == [] and not api.path.exists()


def test_agent_creation_failure_rolls_back(agent_approval_api, monkeypatch):
    """申请INSERT后故障应回滚，不报告申请成功或创建检查点。"""
    api = agent_approval_api
    real_add = repository.add_approval
    def fail_after_flush(session, record):
        """真正flush后制造SQL异常。"""
        real_add(session, record)
        raise SQLAlchemyError("internal-create-marker")
    monkeypatch.setattr(repository, "add_approval", fail_after_flush)
    response = send(api)
    assert response.status_code == 500 and "internal-create-marker" not in response.text
    assert records(api) == [] and not api.path.exists()


def test_manager_agent_context_cannot_execute_when_review_races(agent_approval_api, monkeypatch):
    """管理用户发起且启动期间申请获批，执行开关仍阻止Agent工具关闭。"""
    api = agent_approval_api
    api.owner.role = "supervisor"
    api.session.commit()
    real_read = closure.read_approval
    def review_before_read(state, runtime):
        """在编排层检查pending后、图读取前提交真实审批，制造状态变化。"""
        service.review_approval(session=runtime.context.session, approval_id=state["approval_id"],
            request=ApprovalReviewRequest(approved=True, reason="并发审批样本"), reviewer=runtime.context.viewer)
        return real_read(state, runtime)
    with monkeypatch.context() as patch:
        patch.setattr(closure, "read_approval", review_before_read)
        response = send(api)
    assert response.status_code == 200 and "尚未确认" in response.json()["answer"]
    stored = records(api)
    assert len(stored) == 1 and stored[0].status == "approved"
    assert api.session.get(TicketRecord, api.ticket.id).status == "open"
    assert api.session.scalars(select(ApprovalExecutionRecord)).all() == []
    retried = api.client.post(f"/v1/approvals/{stored[0].id}/workflow/run", headers=headers(api.manager), json={"action": "retry"})
    assert retried.status_code == 200 and retried.json()["approval"]["status"] == "succeeded"
