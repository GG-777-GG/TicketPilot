# 导入 json，用来把 JSON 字符串转换回 Python 字典
import json

# 导入 pytest，用于 monkeypatch 和异常测试
import pytest

# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入需要测试的 Agent Loop 模块
from ticketpilot.agent import loop as agent_loop

# 导入测试使用的 Fake Model
from ticketpilot.agent.model import FakeAgentModel

# 导入测试需要的数据模型
from ticketpilot.agent.schemas import (
    FinalAnswerDecision,
    GetTicketToolResult,
    TicketToolData,
    ToolCallDecision,
    ToolRequest,
    KnowledgeToolMatch,
    SearchKnowledgeToolResult,
)

# MagicMock 用来模拟 Embedding Provider。
from unittest.mock import MagicMock

# 导入统一的向量模型接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

# 这个函数负责创建 Agent Loop 测试使用的向量模型。
def build_test_embedding_provider() -> (
    EmbeddingProvider
):
    return MagicMock(
        spec=EmbeddingProvider
    )


# 验证模型直接回答时不会执行任何工具
def test_run_agent_returns_direct_answer_without_tool(
    monkeypatch,
) -> None:
    # 保存 Dispatcher 收到的工具请求
    tool_requests: list[ToolRequest] = []

    # 定义假的 Dispatcher
    def fake_dispatch_tool(
            context: (
                    agent_loop
                    .agent_dispatcher
                    .AgentToolContext
            ),
            request: ToolRequest,
    ) -> GetTicketToolResult:
        # 如果被调用，就记录工具请求
        tool_requests.append(
            request,
        )

        # 返回测试结果
        return GetTicketToolResult(
            found=False,
            message="不应该执行工具",
            ticket=None,
        )

    # 临时替换真正的 Dispatcher
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    # 模型第一次就直接回答
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="你好，我是 TicketPilot。",
            ),
        ],
    )
    embedding_provider = (
        build_test_embedding_provider()
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 运行 Agent Loop
        result = agent_loop.run_agent(
            session,
            model,
            "你好",
            embedding_provider=(
                embedding_provider
            ),
        )

    # 应该返回模型给出的最终回答
    assert result.answer == "你好，我是 TicketPilot。"

    # 没有执行知识工具时不应该有来源。
    assert result.sources == ()

    # 最终回答不需要调用工具
    assert tool_requests == []

    # 模型只应该被调用一次
    assert len(model.received_messages) == 1

    # 模型第一次只应该看到用户消息
    assert [
        message.role
        for message in model.received_messages[0]
    ] == [
        "user",
    ]


# 验证 Agent 会先执行工具，再根据结果回答用户
def test_run_agent_calls_tool_then_returns_answer(
    monkeypatch,
) -> None:
    # 创建模型第一次返回的工具调用决定
    tool_decision = ToolCallDecision(
        kind="tool_call",
        tool_request=ToolRequest(
            name="get_ticket_details",
            arguments={
                "ticket_id": "ticket-001",
            },
        ),
    )

    # 创建模型第二次返回的最终回答
    final_decision = FinalAnswerDecision(
        kind="final_answer",
        answer="ticket-001 当前正在处理中。",
    )

    # 创建依次返回两个决定的 Fake Model
    model = FakeAgentModel(
        decisions=[
            tool_decision,
            final_decision,
        ],
    )

    # 创建假的工具执行结果
    tool_result = GetTicketToolResult(
        found=True,
        message="已找到工单",
        ticket=TicketToolData(
            id="ticket-001",
            subject="无法登录系统",
            description="输入密码后无法登录",
            priority="high",
            status="in_progress",
            assigned_to="测试客服",
        ),
    )

    # 保存 Dispatcher 收到的 Session
    received_contexts: list[
        agent_loop.agent_dispatcher.AgentToolContext
    ] = []

    # 保存 Dispatcher 收到的工具请求
    received_requests: list[ToolRequest] = []

    # 定义假的 Dispatcher
    def fake_dispatch_tool(
            context: (
                    agent_loop
                    .agent_dispatcher
                    .AgentToolContext
            ),
            request: ToolRequest,
    ) -> GetTicketToolResult:
        # 记录 Agent Loop 传入的 Session
        received_contexts.append(
            context,
        )

        # 记录 Agent Loop 传入的工具请求
        received_requests.append(
            request,
        )

        # 返回提前准备好的工具结果
        return tool_result

    # 临时替换真正的 Dispatcher
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    embedding_provider = (
        build_test_embedding_provider()
    )
    # 创建并自动关闭测试 Session
    with Session() as session:
        # 运行完整 Agent Loop
        result = agent_loop.run_agent(
            session,
            model,
            "查询 ticket-001",
            embedding_provider=(
                embedding_provider
            ),
        )

    # 应该返回模型第二次给出的最终回答
    assert result.answer == "ticket-001 当前正在处理中。"

    # 只查询工单时不会产生知识库来源。
    assert result.sources == ()

    # Dispatcher 应该只执行一次
    assert len(received_requests) == 1

    # 工具上下文应该保存原来的数据库 Session。
    assert received_contexts[0].session is session

    # 工具上下文应该保存传给 Loop 的 Provider。
    assert (
            received_contexts[0].embedding_provider
            is embedding_provider
    )

    # 工具名称应该正确
    assert (
        received_requests[0].name
        == "get_ticket_details"
    )

    # 模型应该被调用两次
    assert len(model.received_messages) == 2

    # 第二次调用模型时应该有三条消息
    assert [
        message.role
        for message in model.received_messages[1]
    ] == [
        "user",
        "assistant",
        "tool",
    ]

    # 取得第二次模型调用时看到的工具结果消息
    tool_message = model.received_messages[1][2]

    # 将工具结果 JSON 转换成普通字典
    tool_data = json.loads(
        tool_message.content,
    )

    # 模型应该能看到工单状态
    assert (
        tool_data["ticket"]["status"]
        == "in_progress"
    )

    # 工具结果中不能出现客户邮箱
    assert (
        "contact_email"
        not in tool_data["ticket"]
    )


# 验证 Agent 不会无限执行工具
def test_run_agent_stops_at_tool_call_limit(
    monkeypatch,
) -> None:
    # 创建一个重复使用的工具调用决定
    tool_decision = ToolCallDecision(
        kind="tool_call",
        tool_request=ToolRequest(
            name="get_ticket_details",
            arguments={
                "ticket_id": "ticket-001",
            },
        ),
    )

    # 模型连续三次都请求调用工具
    model = FakeAgentModel(
        decisions=[
            tool_decision,
            tool_decision,
            tool_decision,
        ],
    )

    # 保存真正被 Dispatcher 执行的工具请求
    executed_requests: list[ToolRequest] = []

    # 定义假的 Dispatcher
    def fake_dispatch_tool(
            context: (
                    agent_loop
                    .agent_dispatcher
                    .AgentToolContext
            ),
            request: ToolRequest,
    ) -> GetTicketToolResult:
        # 记录真正执行的工具请求
        executed_requests.append(
            request,
        )

        # 返回测试工具结果
        return GetTicketToolResult(
            found=False,
            message="工单不存在",
            ticket=None,
        )

    # 临时替换真正的 Dispatcher
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    embedding_provider = (
        build_test_embedding_provider()
    )

    embedding_provider = (
        build_test_embedding_provider()
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 最多只允许真正执行两次工具
        with pytest.raises(
            agent_loop.AgentLoopLimitError,
            match="工具调用次数超过限制",
        ):
            # 第三次工具请求应该在执行前被拒绝
            agent_loop.run_agent(
                session,
                model,
                "反复查询工单",
                embedding_provider=(
                    embedding_provider
                ),
                max_tool_calls=2,
            )

    # 模型一共做出了三次决定
    assert len(model.received_messages) == 3

    # 但 Dispatcher 最多只执行了两次
    assert len(executed_requests) == 2

# 这个测试负责验证知识检索结果会重新交给模型回答。
def test_run_agent_passes_knowledge_result_back_to_model(
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

    # 模型读取工具结果后生成最终回答。
    final_decision = FinalAnswerDecision(
        kind="final_answer",
        answer=(
            "可以通过注册邮箱重置密码。"
            "来源：密码重置说明。"
        ),
    )

    # Fake Model 会依次返回工具调用和最终回答。
    model = FakeAgentModel(
        decisions=[
            tool_decision,
            final_decision,
        ],
    )

    # 准备假的知识检索工具结果。
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="已找到 1 条相关企业知识",
        query="如何重置密码？",
        matches=[
            KnowledgeToolMatch(
                chunk_id="chunk-001",
                document_id="document-001",
                document_title="密码重置说明",
                document_version=1,
                source_type="manual",
                source_uri=None,
                chunk_index=0,
                content=(
                    "用户可以通过注册邮箱重置密码。"
                ),
                start_offset=0,
                end_offset=17,
                similarity=0.92,
            ),
        ],
    )

    # 保存 Dispatcher 收到的上下文和工具请求。
    received_contexts: list[
        agent_loop.agent_dispatcher.AgentToolContext
    ] = []
    received_requests: list[
        ToolRequest
    ] = []

    # 定义假的 Dispatcher。
    def fake_dispatch_tool(
        context: (
            agent_loop
            .agent_dispatcher
            .AgentToolContext
        ),
        request: ToolRequest,
    ) -> SearchKnowledgeToolResult:
        received_contexts.append(
            context
        )
        received_requests.append(
            request
        )

        return tool_result

    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    embedding_provider = (
        build_test_embedding_provider()
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            "如何重置密码？",
            embedding_provider=(
                embedding_provider
            ),
        )

    # Agent 应该返回模型读取知识后的最终回答。
    assert result.answer == (
        "可以通过注册邮箱重置密码。"
        "来源：密码重置说明。"
    )

    # 来源必须由真实工具结果生成。
    assert len(result.sources) == 1
    assert result.sources[0].chunk_id == "chunk-001"
    assert (
        result.sources[0].document_title
        == "密码重置说明"
    )
    assert result.sources[0].similarity == pytest.approx(
        0.92
    )

    # Dispatcher 应该执行一次知识检索工具。
    assert len(received_requests) == 1
    assert (
        received_requests[0].name
        == "search_knowledge"
    )

    # 工具上下文应该携带同一个 Provider。
    assert (
        received_contexts[0].embedding_provider
        is embedding_provider
    )

    # 模型应该被调用两次。
    assert len(
        model.received_messages
    ) == 2

    # 第二次模型调用应该包含工具结果。
    second_messages = (
        model.received_messages[1]
    )

    assert [
        message.role
        for message in second_messages
    ] == [
        "user",
        "assistant",
        "tool",
    ]

    # 读取发送给模型的工具结果 JSON。
    tool_data = json.loads(
        second_messages[2].content
    )

    assert tool_data["found"] is True
    assert (
        tool_data["matches"][0]["document_title"]
        == "密码重置说明"
    )
    assert (
        tool_data["matches"][0]["content"]
        == "用户可以通过注册邮箱重置密码。"
    )
    assert (
        tool_data["matches"][0]["similarity"]
        == pytest.approx(0.92)
    )
