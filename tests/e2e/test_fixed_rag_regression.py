"""固定完整 Agent 回归：真实检索、生成、校验与最终引用，只读知识库。"""

import os
from time import perf_counter

import pytest

from ticketpilot.agent.loop import run_agent
from ticketpilot.agent.providers.ollama import OllamaModel
from ticketpilot.agent.verification import OllamaEvidenceVerifier
from ticketpilot.core.database import SessionLocal
from ticketpilot.knowledge.embeddings.ollama import OllamaEmbeddingProvider


@pytest.mark.e2e
@pytest.mark.postgres
@pytest.mark.ollama
@pytest.mark.parametrize(
    ("question", "source_uri", "anchors"),
    [
        pytest.param(
            "根据维修合同，维修部分的保修从哪个时间点起算？适用于什么情况？期限多久？",
            "2323.docx",
            ("送货日起", "非人为故障", "叁个月"),
            id="contract-warranty",
        ),
        pytest.param(
            "根据维修合同，维修人员的手机号码是多少？",
            None,
            (),
            id="contract-mobile-missing",
        ),
        pytest.param(
            "根据 PDF 学习笔记，完成新功能的第一步是什么？",
            "TicketPilot_项目学习笔记_当前进度_2026-09-21_含本章答案.pdf",
            ("先写业务流程和职责边界",),
            id="study-first-step",
        ),
    ],
)
def test_fixed_rag_regression(
    question: str,  # 发送给正式 Agent 的固定问题。
    source_uri: str | None,  # 正例的正确文件名；None 表示本题应该拒答。
    anchors: tuple[str, ...],  # 正例答案必须包含的事实，仅作为测试评分依据。
) -> None:
    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("设置 TICKETPILOT_RUN_LIVE_EVAL=1 后运行")
    started = perf_counter()
    try:
        with SessionLocal() as session:
            result = run_agent(
                session=session,
                model=OllamaModel(),
                user_message=question,
                embedding_provider=OllamaEmbeddingProvider(),
                allowed_visibilities=("internal",),
                require_knowledge_search=True,
                # 明确接入正式校验器，不依赖开发者当前 .env 开关。
                evidence_verifier=OllamaEvidenceVerifier(),
            )
        if source_uri is None:
            assert result.answer == "根据当前检索结果，无法确认答案。"
            assert not result.sources
        else:
            assert any(source.source_uri == source_uri for source in result.sources)
            assert all(anchor in result.answer for anchor in anchors), result.answer
    finally:
        # 即使失败也打印耗时；不把失败误记为通过。
        print(f"完整问答耗时={perf_counter() - started:.2f}s", flush=True)
