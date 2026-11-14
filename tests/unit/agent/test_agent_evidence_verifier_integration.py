"""Agent Loop 对可选证据校验器的调用约定。"""

import pytest
from sqlalchemy.orm import Session
from unittest.mock import MagicMock

from ticketpilot.agent import loop as agent_loop
from ticketpilot.agent.model import FakeAgentModel
from ticketpilot.agent.schemas import (
    FinalAnswerDecision,
    KnowledgeToolMatch,
    SearchKnowledgeToolResult,
)
from ticketpilot.knowledge.embeddings.base import EmbeddingProvider


def build_test_embedding_provider() -> EmbeddingProvider:
    return MagicMock(spec=EmbeddingProvider)


class FakeEvidenceVerifier:
    """只记录调用和返回预设结果，不连接 Ollama。"""

    def __init__(self, supported: bool) -> None:
        self.supported = supported
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def verify(self, *, question: str, evidence: tuple[str, ...]) -> bool:
        self.calls.append((question, evidence))
        return self.supported


@pytest.mark.parametrize("line_break", ["\n", "\r\n"])
@pytest.mark.parametrize("supported", [True, False])
def test_merged_sentences_use_original_text_for_verifier_and_answer(
    monkeypatch: pytest.MonkeyPatch,
    line_break: str,
    supported: bool,
) -> None:
    """复现演示文档：模型省略换行时，校验器和用户仍收到原文。"""
    question = "换货申请应在多少天内提交？超过期限如何处理？"
    first = "换货申请应在签收后11个自然日内提交。"
    second = "超过11个自然日,应转人工复核。"
    original = first + line_break + second
    model = FakeAgentModel(decisions=[FinalAnswerDecision(
        kind="final_answer",
        answer="模型自行改写的回答不会直接显示。",
        cited_chunk_ids=["demo-chunk"],
        evidence=[{"chunk_id": "demo-chunk", "quote": first + second}],
        evidence_is_sufficient=True,
    )])
    content = "前端联调演示规则\n\n" + original + "\n线上受理时间为工作日。"
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到演示规则",
        query=question,
        matches=[KnowledgeToolMatch(
            chunk_id="demo-chunk",
            document_id="demo-document",
            document_title="前端联调演示规则",
            document_version=1,
            source_type="file",
            source_uri="前端联调验收.txt",
            chunk_index=0,
            content=content,
            start_offset=0,
            end_offset=len(content),
            similarity=0.85,
        )],
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )
    verifier = FakeEvidenceVerifier(supported)
    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            question,
            embedding_provider=build_test_embedding_provider(),
            require_knowledge_search=True,
            evidence_verifier=verifier,
        )

    assert verifier.calls == [(question, (original,))]
    if supported:
        assert result.answer == "根据知识库原文：\n" + original
        assert [source.document_title for source in result.sources] == [
            "前端联调演示规则",
        ]
    else:
        assert result.answer == "根据当前检索结果，无法确认答案。"
        assert result.sources == ()


@pytest.mark.parametrize("supported", [True, False])
def test_run_agent_uses_optional_evidence_verifier(
    monkeypatch: pytest.MonkeyPatch,
    supported: bool,
) -> None:
    """开启校验时，只把已验证存在于原文中的引句交给它。"""
    question = "保修从什么时候起算？"
    quote = "按送货日起，非人为故障保修叁个月。"
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="保修从送货日起算。",
                cited_chunk_ids=["chunk-001"],
                evidence=[{"chunk_id": "chunk-001", "quote": quote}],
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
                chunk_id="chunk-001",
                document_id="document-001",
                document_title="维修合同",
                document_version=1,
                source_type="file",
                source_uri="contract.docx",
                chunk_index=0,
                content=quote,
                start_offset=0,
                end_offset=len(quote),
                similarity=0.8,
            ),
        ],
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )
    verifier = FakeEvidenceVerifier(supported)

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            question,
            embedding_provider=build_test_embedding_provider(),
            require_knowledge_search=True,
            evidence_verifier=verifier,
        )

    assert verifier.calls == [(question, (quote,))]
    if supported:
        assert result.answer == f"根据知识库原文：\n{quote}"
        assert [source.chunk_id for source in result.sources] == [
            "chunk-001"
        ]
    else:
        assert result.answer == "根据当前检索结果，无法确认答案。"
        assert result.sources == ()


def test_run_agent_does_not_verify_direct_answer() -> None:
    """没有知识库引句的普通对话不需要额外校验调用。"""
    verifier = FakeEvidenceVerifier(supported=False)
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="你好，我是 TicketPilot。",
            ),
        ],
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            "你好",
            embedding_provider=build_test_embedding_provider(),
            evidence_verifier=verifier,
        )

    assert verifier.calls == []
    assert result.answer == "你好，我是 TicketPilot。"


def test_verifier_runs_once_after_all_quotes_are_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个片段应合并成一次校验，而不是每处理一段就校验一次。"""
    question = "密码重置要联系谁，服务台电话是多少？"
    first_quote = "密码重置请联系管理员。"
    second_quote = "IT 服务台电话：400-123-4567。"
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="联系管理员；服务台电话为 400-123-4567。",
                cited_chunk_ids=["chunk-001", "chunk-002"],
                evidence=[
                    {"chunk_id": "chunk-001", "quote": first_quote},
                    {"chunk_id": "chunk-002", "quote": second_quote},
                ],
                evidence_is_sufficient=True,
            ),
        ],
    )
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到两条知识",
        query=question,
        matches=[
            KnowledgeToolMatch(
                chunk_id=f"chunk-{index:03d}",
                document_id=f"document-{index:03d}",
                document_title=f"知识文档 {index}",
                document_version=1,
                source_type="manual",
                source_uri=None,
                chunk_index=0,
                content=quote,
                start_offset=0,
                end_offset=len(quote),
                similarity=0.8,
            )
            for index, quote in enumerate(
                (first_quote, second_quote),
                start=1,
            )
        ],
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )
    verifier = FakeEvidenceVerifier(supported=True)

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            question,
            embedding_provider=build_test_embedding_provider(),
            require_knowledge_search=True,
            evidence_verifier=verifier,
        )

    assert verifier.calls == [
        (question, (first_quote, second_quote)),
    ]
    assert [source.chunk_id for source in result.sources] == [
        "chunk-001",
        "chunk-002",
    ]


def test_invalid_quote_is_rejected_before_verifier_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全部引句完成原文核对之前，不应调用模型校验器。"""
    question = "密码重置要联系谁，服务台电话是多少？"
    first_quote = "密码重置请联系管理员。"
    real_second_quote = "IT 服务台电话：400-123-4567。"
    invented_quote = "IT 服务台电话：400-999-9999。"
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="联系管理员；服务台电话为 400-999-9999。",
                cited_chunk_ids=["chunk-001", "chunk-002"],
                evidence=[
                    {"chunk_id": "chunk-001", "quote": first_quote},
                    {"chunk_id": "chunk-002", "quote": invented_quote},
                ],
                evidence_is_sufficient=True,
            ),
        ],
    )
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到两条知识",
        query=question,
        matches=[
            KnowledgeToolMatch(
                chunk_id=f"chunk-{index:03d}",
                document_id=f"document-{index:03d}",
                document_title=f"知识文档 {index}",
                document_version=1,
                source_type="manual",
                source_uri=None,
                chunk_index=0,
                content=quote,
                start_offset=0,
                end_offset=len(quote),
                similarity=0.8,
            )
            for index, quote in enumerate(
                (first_quote, real_second_quote),
                start=1,
            )
        ],
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )
    verifier = FakeEvidenceVerifier(supported=True)

    with Session() as session:
        with pytest.raises(
            agent_loop.InvalidAgentEvidenceError,
            match="证据引句不在检索原文中",
        ):
            agent_loop.run_agent(
                session,
                model,
                question,
                embedding_provider=build_test_embedding_provider(),
                require_knowledge_search=True,
                evidence_verifier=verifier,
            )

    assert verifier.calls == []


@pytest.mark.parametrize("use_verifier", [False, True])
def test_real_but_irrelevant_quote_needs_answerability_check(
    # pytest 用它把本地 Ollama 工具调用替换成固定结果。
    monkeypatch: pytest.MonkeyPatch,

    # False 模拟当前默认关闭校验；True 模拟明确开启校验。
    use_verifier: bool,
) -> None:
    """引句真的存在于原文，不代表它回答了用户所问的事实。"""

    question = "管理员的电话号码是多少？"
    quote = "TicketPilot password reset: contact administrator."

    # 故意模拟模型把无电话号码的真实引句误判为“证据足够”。
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="请联系管理员。",
                cited_chunk_ids=["chunk-001"],
                evidence=[{"chunk_id": "chunk-001", "quote": quote}],
                evidence_is_sufficient=True,
            ),
        ],
    )
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="找到一条相关原文",
        query=question,
        matches=[
            KnowledgeToolMatch(
                chunk_id="chunk-001",
                document_id="document-001",
                document_title="密码重置说明",
                document_version=1,
                source_type="file",
                source_uri="pdf-test.pdf",
                chunk_index=0,
                content=quote,
                start_offset=0,
                end_offset=len(quote),
                similarity=0.8,
            ),
        ],
    )
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        lambda _context, _request: tool_result,
    )

    # 开启时用可控的假校验器模拟“引句不能回答电话号码”。
    # 条件表达式只改变是否注入校验器，不改变模型或检索结果。
    verifier = FakeEvidenceVerifier(supported=False) if use_verifier else None
    with Session() as session:
        result = agent_loop.run_agent(
            session=session,  # 本次测试使用的数据库会话。
            model=model,  # 故意误判证据充分的假模型。
            user_message=question,  # 用户真正询问的是电话号码。
            embedding_provider=build_test_embedding_provider(),  # 假向量服务。
            require_knowledge_search=True,  # 必须先收到检索结果。
            evidence_verifier=verifier,  # 对比关闭和开启的差别。
        )

    if use_verifier:
        assert verifier is not None
        assert verifier.calls == [(question, (quote,))]
        assert result.answer == "根据当前检索结果，无法确认答案。"
        assert result.sources == ()
    else:
        # 记录当前实现的风险：只验证“引句存在”，不能发现它答非所问。
        assert result.answer == f"根据知识库原文：\n{quote}"
        assert [source.chunk_id for source in result.sources] == ["chunk-001"]
