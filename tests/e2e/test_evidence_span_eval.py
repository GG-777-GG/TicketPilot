"""评测本地模型能否直接摘出回答问题的原文片段。

业务流程：问题与原文 → 模型给出原文片段或 null → 检查片段确实在原文中。
这只是能力评测，不接入正式 Agent；需显式启用真实 Ollama 评测。
"""

import os

import pytest
from ollama import Client
from pydantic import BaseModel, ConfigDict

from ticketpilot.core.config import settings


class EvidenceSpan(BaseModel):
    """answer_span 是能回答问题的原文片段；没有则为 None。"""

    model_config = ConfigDict(extra="forbid")
    answer_span: str | None


SYSTEM_PROMPT = (
    "从原文中摘录能直接回答问题的最短连续原文片段。"
    "必须逐字照抄，不得改写、推断或补充。"
    "若原文没有给出问题所问的具体事实，answer_span 返回 null。"
    "只返回符合 JSON Schema 的结果。"
)

PDF_EVIDENCE = "TicketPilot password reset: contact administrator."
PROCESS_EVIDENCE = (
    "员工忘记密码时,应先在公司登录页面点击忘记密码,"
    "通过企业邮箱接收验证码并设置新密码。"
    "如果无法访问企业邮箱,应联系直属主管确认身份,"
    "再由 IT 服务台处理。"
)
CONTRACT_EVIDENCE = "按送货日起,非人为故障对维修部份保修叁个月。"


@pytest.mark.e2e
@pytest.mark.ollama
@pytest.mark.parametrize(
    ("question", "evidence", "expected_phrase"),
    [
        pytest.param(
            "密码重置应该联系谁？",
            PDF_EVIDENCE,
            "administrator",
            id="pdf-contact-person",
        ),
        pytest.param(
            "管理员的联系电话是多少？",
            PDF_EVIDENCE,
            None,
            id="pdf-phone-number-missing",
        ),
        pytest.param(
            "密码重置需要多久完成？",
            PDF_EVIDENCE,
            None,
            id="pdf-duration-missing",
        ),
        pytest.param(
            "重置密码需要提交哪些材料？",
            PDF_EVIDENCE,
            None,
            id="pdf-materials-missing",
        ),
        pytest.param(
            "忘记密码后首先应该在登录页面做什么？",
            PROCESS_EVIDENCE,
            "点击忘记密码",
            id="process-first-step",
        ),
        pytest.param(
            "重置密码需要提交哪些材料？",
            PROCESS_EVIDENCE,
            None,
            id="process-materials-missing",
        ),
        pytest.param(
            "维修日期具体是哪一天？",
            CONTRACT_EVIDENCE,
            None,
            id="contract-repair-date-missing",
        ),
        pytest.param(
            "保修从哪个时间点起算？",
            CONTRACT_EVIDENCE,
            "送货日起",
            id="contract-correct-start-date",
        ),
    ],
)
def test_ollama_extracts_answer_span(
    question: str,
    evidence: str,
    expected_phrase: str | None,
) -> None:
    """检查能回答时摘录原文、不能回答时返回空值。"""

    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("需要显式启用真实 Ollama 评测")

    response = Client(
        host=settings.ollama_base_url,
        timeout=settings.ollama_timeout_seconds,
    ).chat(
        model=settings.ollama_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"问题：{question}\n原文：{evidence}",
            },
        ],
        format=EvidenceSpan.model_json_schema(),
        options={"temperature": 0},
        think=False,
    )

    span = EvidenceSpan.model_validate_json(
        response.message.content or ""
    ).answer_span
    if expected_phrase is None:
        assert span is None, f"无依据的问题却摘录了：{span!r}"
    else:
        assert span is not None, "有依据的问题被误判为无答案"
        assert span in evidence, f"摘录不是连续原文：{span!r}"
        assert expected_phrase in span, f"摘录未包含正确答案：{span!r}"
