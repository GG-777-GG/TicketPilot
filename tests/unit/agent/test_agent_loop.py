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
            # 主管的检索范围必须传进本次 Agent 运行。
            allowed_visibilities=("internal", "restricted"),
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

    # 工具上下文必须保留 Loop 收到的权限范围。
    assert received_contexts[0].allowed_visibilities == (
        "internal",
        "restricted",
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

    # 模型虽然找到了原文，却把答案改写错了。
    final_decision = FinalAnswerDecision(
        kind="final_answer",
        answer="只能联系管理员重置密码。",
        cited_chunk_ids=["chunk-001"],
        evidence=[
            {"chunk_id": "chunk-001", "quote": "用户可以"},
            {"chunk_id": "chunk-001", "quote": "通过注册邮箱重置密码"},
        ],
        evidence_is_sufficient=True,
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

    # 知识库回答应使用已验证的引句，不采用模型的错误改写。
    assert result.answer == (
        "根据知识库原文：\n"
        "用户可以\n"
        "通过注册邮箱重置密码"
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


# 这个测试负责验证 knowledge 模式先检索，再让模型依据结果回答。
def test_run_agent_forces_knowledge_search_before_model_decides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "维修部分保修多久？"
    content = "按送货日起，非人为故障保修叁个月。"
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="维修部分保修三个月。",
                cited_chunk_ids=["contract-chunk-001"],
                evidence=[{"chunk_id": "contract-chunk-001", "quote": content}],
                evidence_is_sufficient=True,
            ),
        ],
    )
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到合同条款",
        query=question,
        matches=[
            KnowledgeToolMatch(
                chunk_id="contract-chunk-001",
                document_id="contract-001",
                document_title="维修合同",
                document_version=1,
                source_type="file",
                source_uri="contract.docx",
                chunk_index=0,
                content=content,
                start_offset=0,
                end_offset=len(content),
                similarity=0.8,
            ),
        ],
    )
    requests: list[ToolRequest] = []

    def fake_dispatch_tool(
        _context: agent_loop.agent_dispatcher.AgentToolContext,
        request: ToolRequest,
    ) -> SearchKnowledgeToolResult:
        requests.append(request)
        return tool_result

    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            question,
            embedding_provider=build_test_embedding_provider(),
            require_knowledge_search=True,
        )

    assert len(requests) == 1
    assert requests[0].name == "search_knowledge"
    assert requests[0].arguments == {"query": question}
    assert len(model.received_messages) == 1
    assert model.received_allow_tool_calls == [False]
    assert [message.role for message in model.received_messages[0]] == [
        "user",
        "assistant",
        "tool",
    ]
    assert result.answer == f"根据知识库原文：\n{content}"
    assert [source.chunk_id for source in result.sources] == [
        "contract-chunk-001",
    ]


def test_knowledge_mode_does_not_execute_second_tool_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使模型要求再次检索，knowledge 模式也只执行第一次检索。"""

    question = "密码重置应该联系谁？"
    model = FakeAgentModel(
        decisions=[
            ToolCallDecision(
                kind="tool_call",
                tool_request=ToolRequest(
                    name="search_knowledge",
                    arguments={"query": question},
                ),
            ),
        ],
    )
    requests: list[ToolRequest] = []

    def fake_dispatch_tool(
        _context: agent_loop.agent_dispatcher.AgentToolContext,
        request: ToolRequest,
    ) -> SearchKnowledgeToolResult:
        requests.append(request)
        return SearchKnowledgeToolResult(
            found=False,
            message="没有找到相关知识",
            query=question,
            matches=[],
        )

    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            question,
            embedding_provider=build_test_embedding_provider(),
            require_knowledge_search=True,
        )

    assert len(requests) == 1
    assert model.received_allow_tool_calls == [False]
    assert result.answer == "根据当前检索结果，无法确认答案。"
    assert result.sources == ()


# 这个测试负责验证强制检索无结果时也不会采信模型编造的答案。
def test_run_agent_forced_search_without_match_uses_safe_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "管理员的联系电话是多少？"
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="管理员电话是 12345678。",
                cited_chunk_ids=[],
                evidence=[],
            ),
        ],
    )
    requests: list[ToolRequest] = []

    def fake_dispatch_tool(
        _context: agent_loop.agent_dispatcher.AgentToolContext,
        request: ToolRequest,
    ) -> SearchKnowledgeToolResult:
        requests.append(request)
        return SearchKnowledgeToolResult(
            found=False,
            message="没有找到相关知识",
            query=question,
            matches=[],
        )

    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            question,
            embedding_provider=build_test_embedding_provider(),
            require_knowledge_search=True,
        )

    assert len(requests) == 1
    assert requests[0].name == "search_knowledge"
    assert len(model.received_messages) == 1
    assert result.answer == "根据当前检索结果，无法确认答案。"
    assert result.sources == ()


# 这个测试负责确认查过知识库却没有原文引用时，Agent 不会返回模型编造的答案。
@pytest.mark.parametrize(
    "found",
    [True, False],
    ids=["found-but-no-citation", "no-match-no-citation"],
)
def test_run_agent_uses_safe_answer_when_search_has_no_citation(
    monkeypatch: pytest.MonkeyPatch,
    found: bool,
) -> None:
    model = FakeAgentModel(
        decisions=[
            ToolCallDecision(
                kind="tool_call",
                tool_request=ToolRequest(
                    name="search_knowledge",
                    arguments={"query": "维修部分保修多久？"},
                ),
            ),
            FinalAnswerDecision(
                kind="final_answer",
                answer="维修部分保修十年。",
                cited_chunk_ids=[],
                evidence=[],
            ),
        ],
    )
    matches = (
        [
            KnowledgeToolMatch(
                chunk_id="contract-chunk-001",
                document_id="contract-001",
                document_title="维修合同",
                document_version=1,
                source_type="file",
                source_uri="contract.docx",
                chunk_index=0,
                content="按送货日起，非人为故障保修叁个月。",
                start_offset=0,
                end_offset=19,
                similarity=0.8,
            ),
        ]
        if found
        else []
    )
    tool_result = SearchKnowledgeToolResult(
        found=found,
        message="找到合同条款" if found else "没有找到相关知识",
        query="维修部分保修多久？",
        matches=matches,
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            "维修部分保修多久？",
            embedding_provider=build_test_embedding_provider(),
        )

    assert result.answer == "根据当前检索结果，无法确认答案。"
    assert result.sources == ()


# 这个测试负责验证引句虽真实但不足以回答问题时必须拒答。
def test_run_agent_abstains_when_quote_does_not_answer_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = "管理员的联系电话是多少？"
    quote = "TicketPilot password reset: contact administrator."
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="管理员联系电话未在文档中写出。",
                cited_chunk_ids=["pdf-chunk-001"],
                evidence=[{"chunk_id": "pdf-chunk-001", "quote": quote}],
                evidence_is_sufficient=False,
            ),
        ],
    )
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到一条相关知识",
        query=question,
        matches=[
            KnowledgeToolMatch(
                chunk_id="pdf-chunk-001",
                document_id="pdf-document-001",
                document_title="密码重置 PDF",
                document_version=1,
                source_type="file",
                source_uri="pdf-test.pdf",
                chunk_index=0,
                content=quote,
                start_offset=0,
                end_offset=len(quote),
                similarity=0.56,
            ),
        ],
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            question,
            embedding_provider=build_test_embedding_provider(),
            require_knowledge_search=True,
        )

    assert result.answer == "根据当前检索结果，无法确认答案。"
    assert result.sources == ()


@pytest.mark.parametrize(
    ("cited_chunk_ids", "expected_source_ids"),
    [
        (["chunk-002"], ["chunk-002"]),
        ([], []),
        (["chunk-002", "chunk-002"], ["chunk-002"]),
    ],
)
def test_run_agent_returns_only_cited_knowledge_sources(
    monkeypatch: pytest.MonkeyPatch,
    cited_chunk_ids: list[str],
    expected_source_ids: list[str],
) -> None:
    """检索命中不等于引用；重复声明也不能产生重复来源。"""

    model = FakeAgentModel(
        decisions=[
            ToolCallDecision(
                kind="tool_call",
                tool_request=ToolRequest(
                    name="search_knowledge",
                    arguments={"query": "密码重置"},
                ),
            ),
            FinalAnswerDecision(
                kind="final_answer",
                answer="请联系管理员。",
                cited_chunk_ids=cited_chunk_ids,
                evidence=(
                    [{"chunk_id": "chunk-002", "quote": "密码重置说明 2"}]
                    if cited_chunk_ids
                    else []
                ),
                evidence_is_sufficient=bool(cited_chunk_ids),
            ),
        ]
    )
    matches = [
        KnowledgeToolMatch(
            chunk_id=f"chunk-00{index}",
            document_id=f"document-00{index}",
            document_title=f"文档 {index}",
            document_version=1,
            source_type="file",
            source_uri=f"guide-{index}.pdf",
            chunk_index=0,
            content=f"密码重置说明 {index}",
            start_offset=0,
            end_offset=8,
            # 两个片段的页码不同，验证只引用被选中的来源。
            page_numbers=[index, index + 1],
            similarity=0.9 - index / 10,
        )
        for index in (1, 2)
    ]
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到两条知识",
        query="密码重置",
        matches=matches,
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            "如何重置密码？",
            embedding_provider=build_test_embedding_provider(),
        )

    assert [source.chunk_id for source in result.sources] == expected_source_ids
    assert [source.page_numbers for source in result.sources] == (
        [[2, 3]] if expected_source_ids else []
    )


def test_run_agent_rejects_citation_not_returned_by_tool() -> None:
    """模型不能凭空生成知识片段 ID。"""

    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="请联系管理员。",
                cited_chunk_ids=["invented-chunk"],
            ),
        ]
    )

    with Session() as session:
        with pytest.raises(
            agent_loop.InvalidAgentCitationError,
            match="未检索到",
        ):
            agent_loop.run_agent(
                session,
                model,
                "如何重置密码？",
                embedding_provider=build_test_embedding_provider(),
            )


# 验证 Agent 不接受缺失、伪造或指向未引用片段的原文证据。
@pytest.mark.parametrize(
    ("cited_chunk_ids", "evidence", "expected_message"),
    [
        (["chunk-001"], [], "缺少原文证据"),
        (
            ["chunk-001"],
            [{"chunk_id": "chunk-001", "quote": "从维修日期起算"}],
            "不在检索原文中",
        ),
        (
            [],
            [{"chunk_id": "chunk-001", "quote": "按送货日起"}],
            "没有被引用",
        ),
        (
            ["chunk-001"],
            [{"chunk_id": "chunk-002", "quote": "按送货日起"}],
            "没有被引用",
        ),
    ],
)
def test_run_agent_rejects_invalid_answer_evidence(
    monkeypatch: pytest.MonkeyPatch,
    cited_chunk_ids: list[str],
    evidence: list[dict[str, str]],
    expected_message: str,
) -> None:
    model = FakeAgentModel(
        decisions=[
            ToolCallDecision(
                kind="tool_call",
                tool_request=ToolRequest(
                    name="search_knowledge",
                    arguments={"query": "保修从何时起算？"},
                ),
            ),
            FinalAnswerDecision(
                kind="final_answer",
                answer="维修部分保修三个月。",
                cited_chunk_ids=cited_chunk_ids,
                evidence=evidence,
                evidence_is_sufficient=True,
            ),
        ]
    )
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到一条合同条款",
        query="保修从何时起算？",
        matches=[
            KnowledgeToolMatch(
                chunk_id="chunk-001",
                document_id="document-001",
                document_title="维修合同",
                document_version=4,
                source_type="file",
                source_uri="contract.docx",
                chunk_index=0,
                content="按送货日起，非人为故障保修叁个月。",
                start_offset=0,
                end_offset=20,
                similarity=0.8,
            )
        ],
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )

    with Session() as session:
        with pytest.raises(
            agent_loop.InvalidAgentEvidenceError,
            match=expected_message,
        ):
            agent_loop.run_agent(
                session,
                model,
                "保修从何时起算？",
                embedding_provider=build_test_embedding_provider(),
            )


# 验证多条真实引句拼接后超过接口限制时会安全拒绝。
def test_run_agent_rejects_overlong_extractive_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_quote = "a" * 2000
    model = FakeAgentModel(
        decisions=[
            ToolCallDecision(
                kind="tool_call",
                tool_request=ToolRequest(
                    name="search_knowledge",
                    arguments={"query": "查询长文档"},
                ),
            ),
            FinalAnswerDecision(
                kind="final_answer",
                answer="模型的简短回答",
                cited_chunk_ids=["chunk-001"],
                evidence=[
                    {"chunk_id": "chunk-001", "quote": long_quote}
                    for _ in range(5)
                ],
                evidence_is_sufficient=True,
            ),
        ]
    )
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到一条知识",
        query="查询长文档",
        matches=[
            KnowledgeToolMatch(
                chunk_id="chunk-001",
                document_id="document-001",
                document_title="长文档",
                document_version=1,
                source_type="manual",
                source_uri=None,
                chunk_index=0,
                content=long_quote,
                start_offset=0,
                end_offset=2000,
                similarity=0.9,
            )
        ],
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )

    with Session() as session:
        with pytest.raises(
            agent_loop.InvalidAgentEvidenceError,
            match="原文证据过长",
        ):
            agent_loop.run_agent(
                session,
                model,
                "查询长文档",
                embedding_provider=build_test_embedding_provider(),
            )
