"""独立证据校验器的单元测试，不连接真实 Ollama。

业务流程：问题和已核实的原文引句 → 校验器请求模型判断
→ 只有模型确认且支持引句确实来自原文时，才允许回答。
"""

import json

import pytest
from ollama import ChatResponse, Message


class FakeOllamaClient:
    """记录校验请求，并返回测试指定的结构化结果。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def chat(self, **kwargs) -> ChatResponse:
        self.calls.append(kwargs)
        return ChatResponse(
            message=Message(role="assistant", content=self.content)
        )


class FailingOllamaClient:
    """模拟本地 Ollama 连接失败。"""

    def chat(self, **_kwargs) -> ChatResponse:
        raise ConnectionError("本地 Ollama 无法连接")


@pytest.mark.parametrize(
    ("question", "model_output", "expected"),
    [
        pytest.param(
            "密码重置要支付多少费用？",
            '{"answer":null,"quote":null}',
            False,
            id="irrelevant-real-quote-is-not-an-answer",
        ),
        pytest.param(
            "密码重置应该联系谁？",
            '{"answer":"administrator","quote":"contact administrator"}',
            True,
            id="supported-quote-can-answer",
        ),
        pytest.param(
            "密码重置应该联系谁？",
            '{"answer":"IT 服务台","quote":"IT 服务台"}',
            False,
            id="invented-support-quote-is-rejected",
        ),
    ],
)
def test_verifier_only_accepts_supported_real_quote(
    question: str,
    model_output: str,
    expected: bool,
) -> None:
    """模型说“有答案”时，还必须能从原文找到它给出的支持引句。"""

    # 故意在测试运行时导入：业务模块尚待实现，避免阻止其他测试收集。
    from ticketpilot.agent.verification import OllamaEvidenceVerifier

    client = FakeOllamaClient(model_output)
    verifier = OllamaEvidenceVerifier(client=client, model="test-model")
    quote = "TicketPilot password reset: contact administrator."

    result = verifier.verify(
        question=question,
        evidence=(quote,),
    )

    assert result is expected
    # 首次有效就停止；否则只增加一次开启推理的调用。
    assert [call["think"] for call in client.calls] == (
        [False] if expected else [False, True]
    )

    request = client.calls[0]
    assert request["model"] == "test-model"
    assert request["stream"] is False
    assert request["think"] is False
    assert request["options"] == {"temperature": 0}
    assert isinstance(request["format"], dict)

    messages = request["messages"]
    assert isinstance(messages, list)
    assert question in messages[-1]["content"]
    assert quote in messages[-1]["content"]


def test_verifier_rejects_empty_evidence_without_model_call() -> None:
    """没有候选原文时，无需问模型，直接判定不能回答。"""

    from ticketpilot.agent.verification import OllamaEvidenceVerifier

    client = FakeOllamaClient('{"answer":"假的","quote":"假的"}')
    verifier = OllamaEvidenceVerifier(client=client, model="test-model")

    assert verifier.verify(question="费用是多少？", evidence=()) is False
    assert client.calls == []


def test_verifier_reports_ollama_connection_failure() -> None:
    """模型服务故障与“文档没有答案”是两种不同结果。"""
    from ticketpilot.agent.verification import (
        EvidenceVerificationError,
        OllamaEvidenceVerifier,
    )

    verifier = OllamaEvidenceVerifier(
        client=FailingOllamaClient(),
        model="test-model",
    )

    with pytest.raises(
        EvidenceVerificationError,
        match="证据摘录服务暂时不可用",
    ) as error_info:
        verifier.verify(
            question="密码重置应该联系谁？",
            evidence=("请联系管理员。",),
        )

    assert isinstance(error_info.value.__cause__, ConnectionError)


def test_verifier_reports_invalid_model_response() -> None:
    """模型返回坏 JSON 时应报告服务错误，不伪装成证据不足。"""
    from pydantic import ValidationError

    from ticketpilot.agent.verification import (
        EvidenceVerificationError,
        OllamaEvidenceVerifier,
    )

    verifier = OllamaEvidenceVerifier(
        client=FakeOllamaClient("not-json"),
        model="test-model",
    )

    with pytest.raises(
        EvidenceVerificationError,
        match="证据摘录结果格式错误",
    ) as error_info:
        verifier.verify(
            question="密码重置应该联系谁？",
            evidence=("请联系管理员。",),
        )

    assert isinstance(error_info.value.__cause__, ValidationError)


def test_verifier_recovers_on_reasoning_retry() -> None:
    """第一次没有答案，第二次摘录成功，应返回 True。"""
    from ticketpilot.agent.verification import OllamaEvidenceVerifier

    class RetryClient(FakeOllamaClient):
        def chat(self, **kwargs) -> ChatResponse:
            # 用已记录的调用次数，模拟两次不同的模型结果。
            self.content = (
                '{"answer":null,"quote":null}'
                if not self.calls
                else '{"answer":"送货日","quote":"按送货日起"}'
            )
            return super().chat(**kwargs)

    client = RetryClient("")
    verifier = OllamaEvidenceVerifier(client=client, model="test-model")
    assert verifier.verify(
        question="保修从何时起算？",
        evidence=("按送货日起保修三个月。",),
    ) is True
    assert [call["think"] for call in client.calls] == [False, True]


def test_verifier_accepts_sentence_break_difference_in_extraction() -> None:
    """独立摘录模型省略句间换行时也应接受真实、连续的引文。"""
    from ticketpilot.agent.verification import OllamaEvidenceVerifier

    first = "换货申请应在签收后11个自然日内提交。"
    second = "超过11个自然日,应转人工复核。"
    client = FakeOllamaClient(json.dumps({
        "answer": first + second,
        "quote": first + second,
    }))
    verifier = OllamaEvidenceVerifier(client=client, model="test-model")
    assert verifier.verify(
        question="换货期限和逾期处理方式是什么？",
        evidence=(first + "\n" + second,),
    ) is True
    assert [call["think"] for call in client.calls] == [False]


@pytest.mark.parametrize(
    ("model_output", "evidence"),
    [
        ('{"answer":"维修日","quote":"按送货日起"}', ("按送货日起",)),
        ('{"answer":" ","quote":"按送货日起"}', ("按送货日起",)),
        ('{"answer":"送货日","quote":" "}', ("按送货日起",)),
        ('{"answer":"","quote":"按送货日起"}', ("按送货日起",)),
        ('{"answer":"送货日","quote":null}', ("按送货日起",)),
        # 拼接后存在，但没有任何一条原文完整包含该引句，必须拒绝。
        ('{"answer":"甲","quote":"甲\\n\\n乙"}', ("甲", "乙")),
        ('{"answer":"甲。乙","quote":"甲。乙"}', ("甲。", "乙")),
        ('{"answer":"11天","quote":"期限11天"}', ("期限1\n1天",)),
        ('{"answer":"15天","quote":"期限15天"}', ("期限11天",)),
    ],
)
def test_verifier_rejects_invalid_extraction(
    # 模拟模型返回的答案与引句。
    model_output: str,
    # 各条独立的原始证据。
    evidence: tuple[str, ...],
) -> None:
    from ticketpilot.agent.verification import OllamaEvidenceVerifier

    client = FakeOllamaClient(model_output)
    verifier = OllamaEvidenceVerifier(client=client, model="test-model")
    assert verifier.verify(question="从何时起算？", evidence=evidence) is False
    assert [call["think"] for call in client.calls] == [False, True]
