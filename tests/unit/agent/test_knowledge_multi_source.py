"""防止知识问答遗漏同一问题所需的第二条来源。"""

import json
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from ticketpilot.agent import loop as agent_loop
from ticketpilot.agent.model import FakeAgentModel
from ticketpilot.agent.schemas import (
    EvidenceQuote,
    FinalAnswerDecision,
    KnowledgeToolMatch,
    SearchKnowledgeToolResult,
    ToolRequest,
)
from ticketpilot.knowledge.domain.documents import KnowledgeSourceType
from ticketpilot.knowledge.embeddings.base import EmbeddingProvider


def _match(chunk_id: str, content: str) -> KnowledgeToolMatch:
    return KnowledgeToolMatch(
        chunk_id=chunk_id,
        document_id=f"document-{chunk_id}",
        document_title=f"文档 {chunk_id}",
        document_version=1,
        source_type=KnowledgeSourceType.MANUAL,
        source_uri=None,
        chunk_index=0,
        content=content,
        start_offset=0,
        end_offset=len(content),
        similarity=0.7,
    )


def test_knowledge_mode_keeps_both_sources_for_two_part_question(
    monkeypatch,
) -> None:
    """一问涉及两条独立证据时，两条都应可供模型阅读和引用。"""
    first = "通过企业邮箱验证码重置密码。"
    second = "无法访问邮箱时联系直属主管。"
    tool_result = SearchKnowledgeToolResult(
        found=True,
        message="已找到 2 条相关企业知识",
        query="忘记密码且无法访问邮箱怎么办？",
        matches=[_match("reset", first), _match("no-email", second)],
    )

    def fake_dispatch(_context, _request: ToolRequest) -> SearchKnowledgeToolResult:
        return tool_result

    monkeypatch.setattr(agent_loop.agent_dispatcher, "dispatch_tool", fake_dispatch)
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="先重置密码；无法访问邮箱时联系主管。",
                cited_chunk_ids=["reset", "no-email"],
                evidence=[
                    EvidenceQuote(chunk_id="reset", quote=first),
                    EvidenceQuote(chunk_id="no-email", quote=second),
                ],
                evidence_is_sufficient=True,
            )
        ]
    )

    with Session() as session:
        result = agent_loop.run_agent(
            session,
            model,
            "忘记密码且无法访问邮箱怎么办？",
            embedding_provider=MagicMock(spec=EmbeddingProvider),
            require_knowledge_search=True,
        )

    tool_message = model.received_messages[0][-1]
    shown_ids = [
        match["chunk_id"]
        for match in json.loads(tool_message.content)["matches"]
    ]
    assert shown_ids == ["reset", "no-email"]
    assert {source.chunk_id for source in result.sources} == {
        "reset", "no-email"
    }
