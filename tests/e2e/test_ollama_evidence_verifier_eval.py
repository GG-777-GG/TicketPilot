"""用真实 Ollama 检查独立证据校验器，不访问数据库。

默认跳过；设置 TICKETPILOT_RUN_LIVE_EVAL=1 后运行。
"""

import os

import pytest

from ticketpilot.agent.verification import OllamaEvidenceVerifier


@pytest.mark.e2e
@pytest.mark.ollama
@pytest.mark.parametrize(
    ("question", "evidence", "expected"),
    [
        pytest.param(
            "忘记 TicketPilot 密码后应寻求谁的帮助？",
            "TicketPilot password reset: contact administrator.",
            True,
            id="contact-person-paraphrase",
        ),
        pytest.param(
            "管理员的电子邮箱地址是什么？",
            "TicketPilot password reset: contact administrator.",
            False,
            id="admin-email-missing",
        ),
        pytest.param(
            "密码重置要支付多少费用？",
            "TicketPilot password reset: contact administrator.",
            False,
            id="reset-fee-missing",
        ),
        pytest.param(
            "保修从哪个时间点起算？",
            "按送货日起,非人为故障对维修部份保修叁个月。",
            True,
            id="warranty-start-date",
        ),
    ],
)
def test_ollama_evidence_verifier_with_real_model(
    question: str,
    evidence: str,
    expected: bool,
) -> None:
    """真实模型应该区分直接证据和仅仅相关的原文。"""
    if os.environ.get("TICKETPILOT_RUN_LIVE_EVAL") != "1":
        pytest.skip("设置 TICKETPILOT_RUN_LIVE_EVAL=1 后运行")

    verifier = OllamaEvidenceVerifier()
    assert verifier.verify(
        question=question,
        evidence=(evidence,),
    ) is expected
