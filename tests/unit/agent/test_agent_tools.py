# 导入 pytest，用于 monkeypatch 和异常测试
import pytest

# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入需要测试的 Agent 工具模块
from ticketpilot.agent.tools import tickets as agent_tools




# 导入数据库工单 ORM 模型
from ticketpilot.tickets.models import TicketRecord

# MagicMock 用来模拟 Embedding Provider。
from unittest.mock import MagicMock

# 导入知识检索工具模块。
from ticketpilot.agent.tools import (
    knowledge as knowledge_tools,
)

# 导入知识检索工具参数模型。
from ticketpilot.agent.schemas import (
    GetTicketToolArguments,
    SearchKnowledgeToolArguments,
)

# 导入知识来源枚举。
from ticketpilot.knowledge.documents import (
    KnowledgeSourceType,
)

# 导入 Embedding Provider 接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

# 导入 Retrieval Service 的内部结果类型。
from ticketpilot.knowledge.retrieval import (
    RetrievedKnowledgeChunk,
)

# 验证查询到工单时，工具会返回安全的结构化数据
def test_get_ticket_details_returns_safe_ticket_data(
    # pytest 自动提供 monkeypatch
    monkeypatch,
) -> None:
    # 创建一张模拟的数据库工单
    ticket_record = TicketRecord(
        id="ticket-001",
        customer_name="测试客户",
        contact_email="customer@example.com",
        channel="email",
        subject="无法登录系统",
        description="输入密码后仍然无法登录",
        priority="high",
        status="open",
        assigned_to=None,
    )

    # 保存 Repository 实际收到的工单 ID
    received_ticket_ids: list[str] = []

    # 定义假的 Repository 查询函数
    def fake_get_ticket_by_id(
        # 接收工具传入的 Session
        session: Session,

        # 接收工具传入的工单 ID
        ticket_id: str,
    ) -> TicketRecord:
        # 记录工具传入的工单 ID
        received_ticket_ids.append(
            ticket_id,
        )

        # 模拟数据库成功查到工单
        return ticket_record

    # 临时替换真正的 Repository 查询函数
    monkeypatch.setattr(
        agent_tools.repository,
        "get_ticket_by_id",
        fake_get_ticket_by_id,
    )

    # 创建一个测试使用的 Session
    # with 结束后会自动关闭 Session
    with Session() as session:
        # 调用需要测试的查询工单工具
        result = agent_tools.get_ticket_details(
            session,
            GetTicketToolArguments(
                ticket_id="ticket-001",
            ),
        )

    # 工具应该把正确的 ID 交给 Repository
    assert received_ticket_ids == [
        "ticket-001",
    ]

    # 工具应该告诉 AI 已经找到工单
    assert result.found is True

    # 找到工单后 ticket 不应该是 None
    assert result.ticket is not None

    # 返回的工单 ID 应该正确
    assert result.ticket.id == "ticket-001"

    # 返回的工单标题应该正确
    assert result.ticket.subject == "无法登录系统"

    # 将 Pydantic 模型转换成字典
    safe_ticket_data = result.ticket.model_dump()

    # 不应该把客户姓名交给 AI
    assert "customer_name" not in safe_ticket_data

    # 不应该把客户邮箱交给 AI
    assert "contact_email" not in safe_ticket_data


# 验证查询不到工单时，工具会返回 found=False
def test_get_ticket_details_returns_not_found(
    monkeypatch,
) -> None:
    # 定义一个永远返回 None 的假查询函数
    def fake_get_ticket_by_id(
        session: Session,
        ticket_id: str,
    ) -> None:
        # None 表示数据库中没有这张工单
        return None

    # 临时替换真正的 Repository 查询函数
    monkeypatch.setattr(
        agent_tools.repository,
        "get_ticket_by_id",
        fake_get_ticket_by_id,
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 调用查询工具
        result = agent_tools.get_ticket_details(
            session,
            GetTicketToolArguments(
                ticket_id="missing-ticket",
            ),
        )

    # 工具应该明确表示没有找到
    assert result.found is False

    # 没找到时不应该返回工单数据
    assert result.ticket is None

    # 返回的信息应该稳定
    assert result.message == "工单不存在"


# 验证数据库故障不会被错误地转换成“工单不存在”
def test_get_ticket_details_propagates_repository_error(
    monkeypatch,
) -> None:
    # 定义一个模拟数据库故障的 Repository 函数
    def fake_get_ticket_by_id(
        session: Session,
        ticket_id: str,
    ) -> None:
        # 模拟 Repository 抛出的数据库错误
        raise RuntimeError(
            "查询工单失败",
        )

    # 临时替换真正的 Repository 查询函数
    monkeypatch.setattr(
        agent_tools.repository,
        "get_ticket_by_id",
        fake_get_ticket_by_id,
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 要求工具必须继续抛出 RuntimeError
        with pytest.raises(
            RuntimeError,
            match="查询工单失败",
        ):
            # 调用查询工具
            agent_tools.get_ticket_details(
                session,
                GetTicketToolArguments(
                    ticket_id="ticket-001",
                ),
            )

# 这个测试负责验证知识检索工具会返回安全的匹配结果。
def test_search_knowledge_returns_safe_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 创建模拟 Session 和向量模型。
    session = MagicMock(
        spec=Session
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 保存工具传给 Retrieval Service 的参数。
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
                cosine_distance=0.1,
            ),
        )

    # 临时替换真正的 Retrieval Service。
    monkeypatch.setattr(
        knowledge_tools,
        "retrieve_knowledge",
        fake_retrieve_knowledge,
    )

    # 执行知识检索工具。
    result = knowledge_tools.search_knowledge(
        session,
        embedding_provider,
        SearchKnowledgeToolArguments(
            query="如何重置密码？",
            top_k=3,
            min_similarity=0.5,
        ),
    )

    # Retrieval Service 应该只被调用一次。
    assert len(
        received_calls
    ) == 1

    service_call = received_calls[0]

    assert service_call["session"] is session
    assert (
        service_call["embedding_provider"]
        is embedding_provider
    )
    assert (
        service_call["question"]
        == "如何重置密码？"
    )
    assert service_call["top_k"] == 3
    assert (
        service_call["min_similarity"]
        == 0.5
    )

    # 工具应该明确告诉模型找到了知识。
    assert result.found is True
    assert result.message == (
        "已找到 1 条相关企业知识"
    )
    assert result.query == "如何重置密码？"
    assert len(result.matches) == 1

    match = result.matches[0]

    assert match.chunk_id == "chunk-001"
    assert match.document_title == "密码重置说明"
    assert match.content == (
        "用户可以通过注册邮箱重置密码。"
    )

    # similarity = 1 - cosine_distance
    assert match.similarity == pytest.approx(
        0.9
    )

# 这个测试负责验证没有知识匹配时返回 found=False。
def test_search_knowledge_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 模拟 Retrieval Service 返回空元组。
    monkeypatch.setattr(
        knowledge_tools,
        "retrieve_knowledge",
        lambda **kwargs: (),
    )

    result = knowledge_tools.search_knowledge(
        session,
        embedding_provider,
        SearchKnowledgeToolArguments(
            query="不存在的企业知识",
        ),
    )

    assert result.found is False
    assert result.message == (
        "没有找到相关企业知识"
    )
    assert result.query == "不存在的企业知识"
    assert result.matches == []
