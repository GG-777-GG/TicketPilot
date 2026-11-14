"""评测本地模型能否判断原文是否直接回答问题。

这只是模型能力评测，不接入正式 Agent 流程。
须设置 TICKETPILOT_RUN_LIVE_EVAL=1 才会调用 Ollama。
"""

import os

import pytest
from ollama import Client
from pydantic import BaseModel, ConfigDict

from ticketpilot.core.config import settings


class EvidenceVerdict(BaseModel):
    """模型对一组问题和原文作出的二元判断。"""

    model_config = ConfigDict(extra="forbid")
    supported: bool


class EvidenceVerdictWithQuote(BaseModel):
    """同时返回能否回答，以及作为依据的连续原文。"""

    model_config = ConfigDict(extra="forbid")
    supported: bool
    quote: str | None


SYSTEM_PROMPT = (
    "判断原文是否直接给出了问题所问的具体事实。"
    "仅仅主题相关不算，不能推断或补充。"
    "只返回符合 JSON Schema 的结果。"
)

SYSTEM_PROMPT_WITH_QUOTE = (
    "核对原文是否直接给出了问题所需的具体事实。"
    "先找出问题在问什么，再在原文中寻找能够回答它的连续原文片段。"
    "同一主题但没有所问事实，不算能够回答。"
    "如果能回答，supported 为 true，quote 逐字复制最短的支持片段；"
    "如果不能回答，supported 为 false，quote 为 null。"
    "允许问题和原文采用不同的同义说法，但不得使用原文之外的知识。"
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
SUPPORT_EVIDENCE = (
    "IT 服务台电话: 400-123-4567。"
    "服务时间: 工作日 09:00-18:00。"
)
PAYMENT_EVIDENCE = (
    "付款方式: 开具发票后十五个工作日内付清货款。"
    "交货地点: 乙方所在地。"
)


EVIDENCE_CASES = [
        pytest.param(
            "密码重置应该联系谁？",
            PDF_EVIDENCE,
            True,
            id="pdf-contact-person",
        ),
        pytest.param(
            "密码重置时可以联系管理员吗？",
            PDF_EVIDENCE,
            True,
            id="pdf-contact-person-paraphrase",
        ),
        pytest.param(
            "管理员的联系电话是多少？",
            PDF_EVIDENCE,
            False,
            id="pdf-phone-number-missing",
        ),
        pytest.param(
            "密码重置需要多久完成？",
            PDF_EVIDENCE,
            False,
            id="pdf-duration-missing",
        ),
        pytest.param(
            "重置密码需要提交哪些材料？",
            PDF_EVIDENCE,
            False,
            id="pdf-materials-missing",
        ),
        pytest.param(
            "重置密码需要提交哪些材料？",
            PROCESS_EVIDENCE,
            False,
            id="process-materials-missing",
        ),
        pytest.param(
            "无法访问企业邮箱时，先联系谁确认身份？",
            PROCESS_EVIDENCE,
            True,
            id="process-supervisor",
        ),
        pytest.param(
            "重置密码的验证码发送到哪里？",
            PROCESS_EVIDENCE,
            True,
            id="process-code-destination",
        ),
        pytest.param(
            "重置密码验证码的有效期是多久？",
            PROCESS_EVIDENCE,
            False,
            id="process-code-expiry-missing",
        ),
        pytest.param(
            "维修日期具体是哪一天？",
            CONTRACT_EVIDENCE,
            False,
            id="contract-repair-date-missing",
        ),
        pytest.param(
            "保修从哪个时间点起算？",
            CONTRACT_EVIDENCE,
            True,
            id="contract-correct-start-date",
        ),
        pytest.param(
            "非人为故障的维修部分保修多久？",
            CONTRACT_EVIDENCE,
            True,
            id="contract-warranty-duration",
        ),
        pytest.param(
            "这次维修费用多少钱？",
            CONTRACT_EVIDENCE,
            False,
            id="contract-price-missing",
        ),
        pytest.param(
            "IT 服务台的电话号码是多少？",
            SUPPORT_EVIDENCE,
            True,
            id="support-phone-number",
        ),
        pytest.param(
            "IT 服务台的服务时间是什么时候？",
            SUPPORT_EVIDENCE,
            True,
            id="support-business-hours",
        ),
        pytest.param(
            "IT 服务台平均多久处理完故障？",
            SUPPORT_EVIDENCE,
            False,
            id="support-resolution-time-missing",
        ),
        pytest.param(
            "货款应在开具发票后多久付清？",
            PAYMENT_EVIDENCE,
            True,
            id="payment-deadline",
        ),
        pytest.param(
            "交货地点在哪里？",
            PAYMENT_EVIDENCE,
            True,
            id="payment-delivery-location",
        ),
        pytest.param(
            "货款应该汇入哪个银行账户？",
            PAYMENT_EVIDENCE,
            False,
            id="payment-bank-account-missing",
        ),
        # 下面三题来自完整 RAG 测试中的新问法；这里仅评估独立证据检查。
        pytest.param(
            "忘记 TicketPilot 密码后应寻求谁的帮助？",
            PDF_EVIDENCE,
            True,
            id="holdout-contact-person-paraphrase",
        ),
        pytest.param(
            "管理员的电子邮箱地址是什么？",
            PDF_EVIDENCE,
            False,
            id="holdout-admin-email-missing",
        ),
        pytest.param(
            "密码重置要支付多少费用？",
            PDF_EVIDENCE,
            False,
            id="holdout-reset-fee-missing",
        ),
]

# 评测集的标准答案片段，只用于测试；不是生产环境的字段规则。
# 对正例既验证 supported=True，也验证模型交出的引句包含正确事实。
EXPECTED_QUOTE_ANCHORS = {
    "密码重置应该联系谁？": "administrator",
    "密码重置时可以联系管理员吗？": "administrator",
    "无法访问企业邮箱时，先联系谁确认身份？": "直属主管",
    "重置密码的验证码发送到哪里？": "企业邮箱",
    "保修从哪个时间点起算？": "送货日起",
    "非人为故障的维修部分保修多久？": "叁个月",
    "IT 服务台的电话号码是多少？": "400-123-4567",
    "IT 服务台的服务时间是什么时候？": "工作日 09:00-18:00",
    "货款应在开具发票后多久付清？": "十五个工作日",
    "交货地点在哪里？": "乙方所在地",
    "忘记 TicketPilot 密码后应寻求谁的帮助？": "administrator",
}


@pytest.mark.e2e
@pytest.mark.ollama
@pytest.mark.parametrize(
    ("question", "evidence", "expected_supported"),
    EVIDENCE_CASES,
)
def test_ollama_judges_answerability(
    question: str,
    evidence: str,
    expected_supported: bool,
) -> None:
    """同时检查无依据时拒答和有依据时不误拒。"""

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
                "content": f"问题：{question} 原文：{evidence}",
            },
        ],
        format=EvidenceVerdict.model_json_schema(),
        options={"temperature": 0},
        think=False,
    )

    verdict = EvidenceVerdict.model_validate_json(
        response.message.content or ""
    )
    assert verdict.supported is expected_supported


@pytest.mark.e2e
@pytest.mark.ollama
@pytest.mark.parametrize(
    ("question", "evidence", "expected_supported"),
    EVIDENCE_CASES,
)
@pytest.mark.parametrize(
    "thinking",
    [False, True],
    ids=["direct", "reasoned"],
)
def test_ollama_judges_answerability_with_quote(
    question: str,
    evidence: str,
    expected_supported: bool,
    thinking: bool,
) -> None:
    """比较直接生成与推理模式下的“判断＋原文引句”。"""

    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("需要显式启用真实 Ollama 评测")

    response = Client(
        host=settings.ollama_base_url,
        timeout=settings.ollama_timeout_seconds,
    ).chat(
        model=settings.ollama_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_WITH_QUOTE},
            {
                "role": "user",
                "content": f"问题：{question}\n原文：{evidence}",
            },
        ],
        format=EvidenceVerdictWithQuote.model_json_schema(),
        options={"temperature": 0},
        think=thinking,
    )

    verdict = EvidenceVerdictWithQuote.model_validate_json(
        response.message.content or ""
    )
    assert verdict.supported is expected_supported, (
        f"预期 supported={expected_supported}，"
        f"实际 supported={verdict.supported}，quote={verdict.quote!r}"
    )
    if verdict.supported:
        assert verdict.quote and verdict.quote in evidence, (
            f"支持引句不在原文中：{verdict.quote!r}"
        )
        expected_phrase = EXPECTED_QUOTE_ANCHORS[question]
        assert expected_phrase in verdict.quote, (
            f"引句没有回答所问事实：{verdict.quote!r}"
        )
    else:
        assert verdict.quote is None
