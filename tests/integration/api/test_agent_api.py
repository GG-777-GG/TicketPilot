"""
Agent 对话 API 测试。

测试目标：
验证 POST /v1/agent/chat 的 HTTP 边界、身份认证、
Agent 调用和错误转换是否正确。

测试流程：
1. 使用 dependency_overrides 替换数据库 Session。
2. 替换当前登录用户，避免依赖真实 JWT。
3. 替换 OllamaModel，避免测试连接真实模型。
4. 使用 TestClient 发送 HTTP 请求。
5. 验证状态码、响应 JSON 和 Agent 收到的消息。

覆盖场景：
- 登录用户可以正常请求 Agent。
- 空白问题返回 422。
- 未登录用户返回 401。
- Ollama 服务错误返回 503。
"""

# Generator 用来标注包含 yield 的 Session 依赖
from collections.abc import Generator

# 导入固定测试时间需要的工具
from datetime import UTC, datetime

# pytest 用来创建自动清理依赖覆盖的 fixture
import pytest

# TestClient 用来模拟客户端 HTTP 请求
from fastapi.testclient import TestClient

# Session 是 Agent 工具使用的数据库会话类型
from sqlalchemy.orm import Session

# 导入 FastAPI 应用
from ticketpilot.app import app

# 导入模型依赖工厂
from ticketpilot.agent.dependencies import (
    get_agent_model,
)

# 导入 Fake Model 和统一模型接口
from ticketpilot.agent.model import (
    AgentModel,
    FakeAgentModel,
)

# 导入 Ollama 统一异常
from ticketpilot.agent.providers.ollama import (
    OllamaModelError,
)

# 导入测试需要的 Agent 数据类型
from ticketpilot.agent.schemas import (
    AgentMessage,
    FinalAnswerDecision,
    ModelDecision,
    ToolCallDecision,
    ToolRequest,
)

# 导入数据库 Session 依赖函数
from ticketpilot.core.database import (
    get_session,
)

# 导入当前用户依赖函数
from ticketpilot.identity.dependencies import (
    get_current_user,
)

# 导入用户 ORM 模型
from ticketpilot.identity.models import (
    UserRecord,
)

# MagicMock 用来模拟 Embedding Provider。
from unittest.mock import MagicMock

# 导入真正的 Embedding Provider 依赖工厂。
from ticketpilot.knowledge.dependencies import (
    get_embedding_provider,
)

# 导入统一的向量模型接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

# json 用来读取 Agent Loop 产生的工具消息。
import json

# 导入知识检索工具模块，测试会替换其中的 Retrieval Service。
from ticketpilot.agent.tools import (
    knowledge as knowledge_tools,
)

# 导入知识来源枚举。
from ticketpilot.knowledge.documents import (
    KnowledgeSourceType,
)

# 导入内部知识检索结果类型。
from ticketpilot.knowledge.retrieval import (
    RetrievedKnowledgeChunk,
)


# 创建模拟 HTTP 客户端
client = TestClient(app)


# 创建一个固定的测试登录用户
TEST_USER = UserRecord(
    id="agent-api-test-user",
    email="agent-api@example.com",
    display_name="Agent API 测试用户",
    password_hash="not-used-in-this-test",
    role="agent",
    is_active=True,
    created_at=datetime(
        2026,
        1,
        1,
        tzinfo=UTC,
    ),
)


# 每个测试结束后清理 FastAPI 依赖覆盖
@pytest.fixture(
    autouse=True,
)
def clear_dependency_overrides() -> Generator[
    None,
    None,
    None,
]:
    # 测试开始前确保没有旧的覆盖规则
    app.dependency_overrides.clear()

    # 把执行权交给当前测试
    yield

    # 测试结束后恢复应用状态
    app.dependency_overrides.clear()


# 创建测试专用数据库 Session
def fake_get_session() -> Generator[
    Session,
    None,
    None,
]:
    # 本测试不执行真实 SQL，因此 Session 不需要绑定 Engine
    with Session() as session:
        yield session


# 模拟 JWT 已经验证成功
def fake_get_current_user() -> UserRecord:
    # 直接返回固定测试用户
    return TEST_USER


# 为一个测试配置认证、Session、对话模型和向量模型依赖。
def configure_agent_dependencies(
    model: AgentModel,

    # 测试可以传入指定 Provider；
    # 不传时自动创建 MagicMock。
    embedding_provider: (
        EmbeddingProvider | None
    ) = None,
) -> EmbeddingProvider:
    # 定义本次测试使用的对话模型工厂。
    def fake_get_agent_model() -> AgentModel:
        return model

    # 没有显式提供时创建 Fake Provider。
    selected_embedding_provider = (
        embedding_provider
        if embedding_provider is not None
        else MagicMock(
            spec=EmbeddingProvider
        )
    )

    # 定义本次测试使用的向量模型工厂。
    def fake_get_embedding_provider() -> (
        EmbeddingProvider
    ):
        return selected_embedding_provider

    # 不创建真实数据库 SessionLocal。
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    # 不执行真实 JWT 验证。
    app.dependency_overrides[
        get_current_user
    ] = fake_get_current_user

    # 不创建真实 OllamaModel。
    app.dependency_overrides[
        get_agent_model
    ] = fake_get_agent_model

    # 不创建真实 OllamaEmbeddingProvider。
    app.dependency_overrides[
        get_embedding_provider
    ] = fake_get_embedding_provider

    # 返回 Provider，方便知识工具测试继续设置行为。
    return selected_embedding_provider


# 模拟 Ollama 服务调用失败的模型
class UnavailableAgentModel:
    # 方法格式与 AgentModel Protocol 保持一致
    def decide(
        self,
        messages: list[AgentMessage],
    ) -> ModelDecision:
        # 模拟 Ollama 无法连接
        raise OllamaModelError(
            "测试中的 Ollama 服务不可用",
        )


# 验证登录用户可以正常调用 Agent
def test_agent_chat_returns_answer() -> None:
    # Fake Model 第一次调用就直接回答
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="你好，我是 TicketPilot。",
            ),
        ],
    )

    # 把真实外部依赖替换成测试对象
    configure_agent_dependencies(
        model,
    )

    # 模拟客户端发送 Agent 对话请求
    response = client.post(
        "/v1/agent/chat",
        json={
            "message": "  你好  ",
        },
    )

    # 接口应该成功
    assert response.status_code == 200

    # 响应必须符合 AgentChatResponse
    assert response.json() == {
        "answer": "你好，我是 TicketPilot。",
        "sources": [],
    }

    # Fake Model 应该被调用一次
    assert len(model.received_messages) == 1

    # 请求 Schema 应该已经删除问题两端的空格
    assert (
        model.received_messages[0][0].content
        == "你好"
    )


# 验证空白问题在进入 Agent 前就被拒绝
def test_agent_chat_rejects_blank_message() -> None:
    # 准备一个不应该被调用的 Fake Model
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="不应该返回这个回答",
            ),
        ],
    )
    configure_agent_dependencies(
        model,
    )

    # 发送全部由空格组成的问题
    response = client.post(
        "/v1/agent/chat",
        json={
            "message": "   ",
        },
    )

    # Pydantic 请求校验失败
    assert response.status_code == 422

    # 请求不合法时，不应该进入 Agent Loop
    assert model.received_messages == []


# 验证未登录用户不能调用 Agent
def test_agent_chat_requires_authentication() -> None:
    # 只替换 Session，不替换 get_current_user
    # 因此请求会执行真实的认证检查
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    # 请求故意不携带 Authorization 请求头
    response = client.post(
        "/v1/agent/chat",
        json={
            "message": "你好",
        },
    )

    # 缺少身份认证应该返回 401
    assert response.status_code == 401

    # 返回项目统一的认证错误
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


# 验证 Ollama 故障会转换成稳定的 HTTP 503
def test_agent_chat_returns_503_when_ollama_is_unavailable() -> None:
    # 使用一个调用时必定失败的模型
    model = UnavailableAgentModel()
    configure_agent_dependencies(
        model,
    )

    # 发送格式正确的请求
    response = client.post(
        "/v1/agent/chat",
        json={
            "message": "帮我查询工单",
        },
    )

    # 模型服务不可用应该返回 503
    assert response.status_code == 503

    # 不向客户端暴露底层连接错误
    assert response.json() == {
        "detail": "AI 模型服务暂时不可用",
    }

# 这个测试负责验证 HTTP 请求能够走完整知识工具流程。
def test_agent_chat_uses_knowledge_search_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 模型第一次决定调用知识检索工具。
    tool_decision = ToolCallDecision(
        kind="tool_call",
        tool_request=ToolRequest(
            name="search_knowledge",
            arguments={
                "query": "如何重置密码？",
                "top_k": 3,
                "min_similarity": 0.5,
            },
        ),
    )

    # 模型读取知识结果后生成最终回答。
    final_decision = FinalAnswerDecision(
        kind="final_answer",
        answer=(
            "可以通过注册邮箱重置密码。"
            "来源：密码重置说明。"
        ),
    )

    model = FakeAgentModel(
        decisions=[
            tool_decision,
            final_decision,
        ],
    )

    # 当前 HTTP 请求使用的 Fake Provider。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_agent_dependencies(
        model,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 保存知识工具传给 Retrieval Service 的参数。
    received_calls: list[
        dict[str, object]
    ] = []

    # 模拟 Retrieval Service 返回一条知识片段。
    def fake_retrieve_knowledge(
        **kwargs,
    ) -> tuple[
        RetrievedKnowledgeChunk,
        ...,
    ]:
        received_calls.append(
            kwargs
        )

        return (
            RetrievedKnowledgeChunk(
                chunk_id="chunk-001",
                document_id="document-001",
                document_title="密码重置说明",
                document_version=1,
                source_type=(
                    KnowledgeSourceType.MANUAL
                ),
                source_uri=None,
                chunk_index=0,
                content=(
                    "用户可以通过注册邮箱重置密码。"
                ),
                start_offset=0,
                end_offset=17,
                cosine_distance=0.08,
            ),
        )

    # 保留真实 Router、Loop、Dispatcher 和 Tool，
    # 只替换最底层 Retrieval Service。
    monkeypatch.setattr(
        knowledge_tools,
        "retrieve_knowledge",
        fake_retrieve_knowledge,
    )

    # 发送真实 HTTP 请求。
    response = client.post(
        "/v1/agent/chat",
        json={
            "message": "如何重置密码？",
        },
    )

    assert response.status_code == 200

    assert response.json() == {
        "answer": (
            "可以通过注册邮箱重置密码。"
            "来源：密码重置说明。"
        ),
        "sources": [
            {
                "chunk_id": "chunk-001",
                "document_id": "document-001",
                "document_title": "密码重置说明",
                "document_version": 1,
                "source_type": "manual",
                "source_uri": None,
                "chunk_index": 0,
                "similarity": pytest.approx(0.92),
            },
        ],
    }

    # Retrieval Service 应该执行一次。
    assert len(
        received_calls
    ) == 1

    service_call = received_calls[0]

    assert (
        service_call["question"]
        == "如何重置密码？"
    )
    assert service_call["top_k"] == 3
    assert (
        service_call["min_similarity"]
        == 0.5
    )

    # 必须使用 FastAPI 注入的同一个 Provider。
    assert (
        service_call["embedding_provider"]
        is embedding_provider
    )

    # 模型第一次决定调用工具，第二次生成最终回答。
    assert len(
        model.received_messages
    ) == 2

    # 读取模型第二次收到的工具消息。
    tool_message = (
        model.received_messages[1][2]
    )

    assert tool_message.role == "tool"

    tool_data = json.loads(
        tool_message.content
    )

    assert tool_data["found"] is True
    assert (
        tool_data["matches"][0]["document_title"]
        == "密码重置说明"
    )
    assert (
        tool_data["matches"][0]["similarity"]
        == pytest.approx(0.92)
    )
