"""验证知识片段重排不会漏掉关键证据或改变原始结果。"""

from ticketpilot.knowledge.application.retrieval import (
    RetrievedKnowledgeChunk,
    _character_trigrams,
    rerank_candidates,
)
from ticketpilot.knowledge.domain.documents import KnowledgeSourceType


def _chunk(
    chunk_id: str,
    content: str,
    similarity: float,
) -> RetrievedKnowledgeChunk:
    """构造不依赖数据库的检索结果。"""
    return RetrievedKnowledgeChunk(
        chunk_id=chunk_id,
        document_id="study-notes",
        document_title="PDF 学习笔记",
        document_version=1,
        source_type=KnowledgeSourceType.FILE,
        source_uri="study-notes.pdf",
        chunk_index=0,
        content=content,
        start_offset=0,
        end_offset=len(content),
        page_numbers=(23,),
        cosine_distance=1.0 - similarity,
    )


def test_character_trigrams_normalizes_punctuation() -> None:
    assert _character_trigrams("完成 新功能！") == {
        "完成新",
        "成新功",
        "新功能",
    }
    assert _character_trigrams("好") == set()


def test_rerank_puts_answer_before_distracting_pdf_chunk() -> None:
    # 真实故障的缩小版：向量排名第 1 的片段只谈“下一步”，
    # 排名第 7 的片段才明确写出“完成新功能的第一步”。
    question = "根据 PDF 学习笔记，完成新功能的第一步是什么？"
    candidates = (
        _chunk(
            "distractor",
            "TicketPilot 项目学习笔记。每完成一阶段时记录。"
            "下一步最小可验证目标是什么？",
            0.53,
        ),
        *(
            _chunk(f"other-{index}", f"无关说明{index}", 0.51 - index * 0.01)
            for index in range(1, 6)
        ),
        _chunk(
            "answer",
            "TicketPilot 项目学习笔记。完成一个新功能时的建议顺序："
            "1. 先写业务流程和职责边界，明确输入、输出、正常与异常路径。"
            "自测：ORM 是什么？",
            0.43,
        ),
    )

    results = rerank_candidates(
        question=question,
        candidates=candidates,
        top_k=5,
    )

    assert len(results) == 5
    assert results[0].chunk_id == "answer"
    assert candidates[6].chunk_id == "answer"  # 输入仍保持原向量排序。
    assert all(chunk in candidates for chunk in results)


def test_rerank_keeps_two_chunks_needed_for_a_combined_question() -> None:
    # 不能为了让简单问题更容易回答，就永远只留下一个片段。
    question = "员工忘记密码怎样重置？无法访问邮箱又该怎么办？"
    candidates = (
        _chunk(
            "reset",
            "员工忘记密码时，通过企业邮箱接收验证码并重置密码。",
            0.72,
        ),
        _chunk("general", "员工邮箱与账号的常见问题。", 0.70),
        _chunk(
            "no-email",
            "无法访问企业邮箱时，联系直属主管确认身份，再由 IT 服务台处理。",
            0.66,
        ),
        _chunk("unrelated", "工单状态为已关闭。", 0.55),
    )

    results = rerank_candidates(
        question=question,
        candidates=candidates,
        top_k=3,
    )

    assert {"reset", "no-email"}.issubset(
        {chunk.chunk_id for chunk in results}
    )


def test_rerank_short_question_keeps_vector_order() -> None:
    candidates = (
        _chunk("first", "第一条", 0.8),
        _chunk("second", "第二条", 0.7),
    )

    assert rerank_candidates(
        question="好",
        candidates=candidates,
        top_k=1,
    ) == candidates[:1]
