"""用真实 PostgreSQL 和 Ollama 回归评测 PDF 知识问答。

业务流程：
确认固定测试 PDF 已入库
→ 在 knowledge 模式下发送有答案与无答案两类问题
→ 有答案时核对来源；无答案时检查拒绝编造的行为

这组测试只读取现有知识库，不创建、修改或删除文档。
须设置 TICKETPILOT_RUN_LIVE_EVAL=1 才会执行。
"""

import os
import pytest
from ollama import Client
from sqlalchemy import select

from ticketpilot.agent.loop import AgentLoopLimitError, run_agent
from ticketpilot.agent.model import AgentModel
from ticketpilot.agent.providers.ollama import OllamaModel
from ticketpilot.agent.schemas import AgentMessage, ModelDecision, ToolCallDecision
from ticketpilot.core.database import SessionLocal, engine
from ticketpilot.core.config import settings
from ticketpilot.knowledge.embeddings.ollama import OllamaEmbeddingProvider
from ticketpilot.knowledge.persistence.models import KnowledgeDocumentRecord


PDF_SOURCE_URI = "pdf-test.pdf"
PDF_SAMPLE_CONTENT = "TicketPilot password reset: contact administrator."
UNKNOWN_ANSWER = "根据当前检索结果，无法确认答案。"


class RecordingAgentModel:
    """仅在评测中记录模型决定，方便定位真实调用失败。"""

    def __init__(self, model: AgentModel) -> None:
        self.model = model
        self.decisions: list[ModelDecision] = []

    def decide(
        self,
        messages: list[AgentMessage],
        *,
        allow_tool_calls: bool = True,
    ) -> ModelDecision:
        decision = self.model.decide(
            messages,
            allow_tool_calls=allow_tool_calls,
        )
        self.decisions.append(decision)
        return decision

    def decision_summary(self) -> str:
        """用中文概括每次决定，不输出整段回答或知识原文。"""

        steps = ["后端强制 search_knowledge"]
        for decision in self.decisions:
            if isinstance(decision, ToolCallDecision):
                request = decision.tool_request
                steps.append(f"{request.name}({request.arguments})")
            else:
                steps.append("final_answer")
        return " → ".join(steps)


class ReasoningClient(Client):
    """仅在评测中把 OllamaModel 的调用切换到推理模式。"""

    def chat(self, *args, **kwargs):
        kwargs["think"] = True
        return super().chat(*args, **kwargs)

# 评测数据只描述“问题”和“文档能否回答”，不为电话等字段写专用规则。
# 添加新的业务场景时，往这里增加一个用例即可。
PDF_EVAL_CASES = [
    pytest.param(
        "只根据 pdf-test.pdf 回答：TicketPilot 密码重置应该联系谁？",
        True,
        id="answerable-contact-person",
    ),
    pytest.param(
        "只根据 pdf-test.pdf 回答：管理员的联系电话是多少？",
        False,
        id="unanswerable-phone-number",
    ),
    pytest.param(
        "只根据 pdf-test.pdf 回答：密码重置需要多久完成？",
        False,
        id="unanswerable-duration",
    ),
    pytest.param(
        "只根据 pdf-test.pdf 回答：重置密码需要提交哪些材料？",
        False,
        id="unanswerable-required-documents",
    ),
]

# 这些问题没有参与前面的提示词实验，用于检查新问法能否泛化。
PDF_HOLDOUT_CASES = [
    pytest.param(
        "只根据 pdf-test.pdf 回答：忘记 TicketPilot 密码后应寻求谁的帮助？",
        True,
        id="holdout-contact-person-paraphrase",
    ),
    pytest.param(
        "只根据 pdf-test.pdf 回答：管理员的电子邮箱地址是什么？",
        False,
        id="holdout-admin-email-missing",
    ),
    pytest.param(
        "只根据 pdf-test.pdf 回答：密码重置要支付多少费用？",
        False,
        id="holdout-reset-fee-missing",
    ),
]


@pytest.fixture(scope="module", autouse=True)
def require_live_eval_environment() -> None:
    """避免普通测试意外调用本地模型或真实数据库。"""

    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("需要显式启用真实 PostgreSQL 与 Ollama 评测")

    if engine.dialect.name != "postgresql":
        pytest.fail("真实 RAG 评测需要项目连接 PostgreSQL")


@pytest.mark.e2e
@pytest.mark.postgres
@pytest.mark.ollama
@pytest.mark.parametrize(
    ("question", "answerable"),
    PDF_EVAL_CASES + PDF_HOLDOUT_CASES,
)
@pytest.mark.parametrize(
    "thinking",
    [False, True],
    ids=["direct", "reasoned"],
)
def test_pdf_rag_answer_and_sources(
    question: str,
    answerable: bool,
    thinking: bool,
) -> None:
    """真实模型应回答有依据的问题，并拒答文档未提供的事实。"""

    with SessionLocal() as session:
        document_ids = set(
            session.scalars(
                select(KnowledgeDocumentRecord.id).where(
                    KnowledgeDocumentRecord.source_uri == PDF_SOURCE_URI,
                    KnowledgeDocumentRecord.raw_content == PDF_SAMPLE_CONTENT,
                    KnowledgeDocumentRecord.status == "ready",
                )
            ).all()
        )
        assert document_ids, (
            "缺少固定测试文档：请先上传包含指定文字的 pdf-test.pdf"
        )

        # 只在测试中替换 Client，正式 OllamaModel 配置保持不变。
        client = (
            ReasoningClient(
                host=settings.ollama_base_url,
                timeout=settings.ollama_timeout_seconds,
            )
            if thinking
            else None
        )
        model = RecordingAgentModel(OllamaModel(client=client))
        try:
            result = run_agent(
                session,
                model,
                question,
                embedding_provider=OllamaEmbeddingProvider(),
                require_knowledge_search=True,
            )
        except AgentLoopLimitError as error:
            pytest.fail(
                f"{error}；模型决定顺序：{model.decision_summary()}",
                pytrace=False,
            )

    if answerable:
        assert PDF_SAMPLE_CONTENT in result.answer
        assert result.sources, "有答案时必须提供可核对的 PDF 来源"
        assert all(source.source_uri == PDF_SOURCE_URI for source in result.sources)
        assert all(source.document_id in document_ids for source in result.sources)
    else:
        assert result.answer == UNKNOWN_ANSWER
        assert result.sources == ()
