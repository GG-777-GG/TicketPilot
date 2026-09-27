# 导入 pytest，用于 monkeypatch 和异常测试
import pytest

# 导入 Pydantic 数据验证异常
from pydantic import ValidationError

# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入需要测试的 Dispatcher 模块
from ticketpilot.agent import dispatcher as agent_dispatcher

# 导入工具请求、参数和结果模型
from ticketpilot.agent.schemas import (
    GetTicketToolArguments,
    GetTicketToolResult,
    ToolRequest,
)

# MagicMock 用来模拟 Embedding Provider。
from unittest.mock import MagicMock

# 导入统一的向量模型接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

from ticketpilot.agent.schemas import (
    GetTicketToolArguments,
    GetTicketToolResult,
    SearchKnowledgeToolArguments,
    SearchKnowledgeToolResult,
    ToolRequest,
)

# 这个函数负责为 Dispatcher 测试创建工具上下文。
def build_test_tool_context(
    session: Session,
) -> agent_dispatcher.AgentToolContext:
    return agent_dispatcher.AgentToolContext(
        session=session,

        # 工单工具不会调用它，
        # 但 AgentToolContext 要求提供完整依赖。
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

# 验证 Dispatcher 会调用正确的工具
def test_dispatch_tool_calls_get_ticket_details(
    monkeypatch,
) -> None:
    # 保存假工具实际收到的参数
    received_arguments: list[
        GetTicketToolArguments
    ] = []

    # 创建假工具准备返回的结果
    expected_result = GetTicketToolResult(
        found=False,
        message="测试工具已执行",
        ticket=None,
    )

    # 定义假的查询工单工具
    def fake_get_ticket_details(
        session: Session,
        arguments: GetTicketToolArguments,
    ) -> GetTicketToolResult:
        # 记录 Dispatcher 传入的参数
        received_arguments.append(
            arguments,
        )

        # 返回提前准备好的结果
        return expected_result

    # 临时替换 Dispatcher 实际使用的查询工具
    monkeypatch.setattr(
        agent_dispatcher.ticket_tools,
        "get_ticket_details",
        fake_get_ticket_details,
    )

    # 模拟 AI 生成一次工具请求
    request = ToolRequest(
        # 故意在工具名称两端添加空格
        name="  get_ticket_details  ",

        # 参数仍然是未经专属模型验证的字典
        arguments={
            "ticket_id": "  ticket-001  ",
        },
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 让 Dispatcher 分发这次工具请求
        context = build_test_tool_context(
            session
        )

        result = agent_dispatcher.dispatch_tool(
            context,
            request,
        )

    # Dispatcher 应该返回假工具提供的结果
    assert result == expected_result

    # 假工具应该只被调用一次
    assert len(received_arguments) == 1

    # 工单 ID 应该经过专属参数模型清理
    assert (
        received_arguments[0].ticket_id
        == "ticket-001"
    )


# 验证参数非法时，Dispatcher 不会执行真正的工具
def test_dispatch_tool_rejects_invalid_arguments(
    monkeypatch,
) -> None:
    # 保存工具调用记录
    tool_calls: list[
        GetTicketToolArguments
    ] = []

    # 定义一个假的查询工单工具
    def fake_get_ticket_details(
        session: Session,
        arguments: GetTicketToolArguments,
    ) -> GetTicketToolResult:
        # 如果执行到了这里，就记录本次调用
        tool_calls.append(
            arguments,
        )

        # 返回一个测试结果
        return GetTicketToolResult(
            found=False,
            message="不应该执行到这里",
            ticket=None,
        )

    # 临时替换真正的查询工具
    monkeypatch.setattr(
        agent_dispatcher.ticket_tools,
        "get_ticket_details",
        fake_get_ticket_details,
    )

    # ToolRequest 只检查外层结构
    request = ToolRequest(
        name="get_ticket_details",
        arguments={
            # 空格清理后会变成空字符串
            "ticket_id": "   ",
        },
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 专属参数模型必须抛出验证异常
        with pytest.raises(
            ValidationError,
        ):
            # 尝试分发非法工具请求
            agent_dispatcher.dispatch_tool(
                session,
                request,
            )

    # 参数验证失败，所以工具一次都不能执行
    assert tool_calls == []


# 验证 Dispatcher 会拒绝未知工具
def test_dispatch_tool_rejects_unknown_tool() -> None:
    # 模拟 AI 幻想出一个危险的未知工具
    request = ToolRequest(
        name="delete_all_database",
        arguments={},
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        context = build_test_tool_context(
            session
        )

        with pytest.raises(
                agent_dispatcher.UnknownToolError,
                match="delete_all_database",
        ):
            agent_dispatcher.dispatch_tool(
                context,
                request,
            )

# 这个测试负责验证 Dispatcher 会分发知识检索工具。
def test_dispatch_tool_calls_search_knowledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 保存知识工具实际收到的参数。
    received_arguments: list[
        SearchKnowledgeToolArguments
    ] = []

    # 创建 Fake Embedding Provider。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 准备模拟工具返回的结果。
    expected_result = SearchKnowledgeToolResult(
        found=False,
        message="没有找到相关企业知识",
        query="如何重置密码？",
        matches=[],
    )

    # 定义假的知识检索工具。
    def fake_search_knowledge(
        session: Session,
        received_provider: EmbeddingProvider,
        arguments: SearchKnowledgeToolArguments,
    ) -> SearchKnowledgeToolResult:
        # 必须收到上下文中的同一个 Provider。
        assert (
            received_provider
            is embedding_provider
        )

        received_arguments.append(
            arguments
        )

        return expected_result

    # 临时替换真正的知识检索工具。
    monkeypatch.setattr(
        agent_dispatcher.knowledge_tools,
        "search_knowledge",
        fake_search_knowledge,
    )

    # 模拟模型生成知识检索工具调用。
    request = ToolRequest(
        name="  search_knowledge  ",
        arguments={
            # 故意添加空格，验证专属 Schema 会清理。
            "query": "  如何重置密码？  ",
            "top_k": 3,
            "min_similarity": 0.5,
        },
    )

    with Session() as session:
        # 使用当前测试专属的 Provider 创建上下文。
        context = (
            agent_dispatcher.AgentToolContext(
                session=session,
                embedding_provider=(
                    embedding_provider
                ),
            )
        )

        result = agent_dispatcher.dispatch_tool(
            context,
            request,
        )

    # Dispatcher 应该返回知识工具提供的结果。
    assert result == expected_result

    # 知识工具应该只被执行一次。
    assert len(
        received_arguments
    ) == 1

    arguments = received_arguments[0]

    # query 两端空格应该已经被清理。
    assert arguments.query == "如何重置密码？"
    assert arguments.top_k == 3
    assert arguments.min_similarity == 0.5
