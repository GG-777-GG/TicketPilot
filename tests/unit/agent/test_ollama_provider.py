import json
import re

import pytest
from httpx import ReadTimeout
from ollama import ChatResponse, Message
from pydantic import TypeAdapter

from ticketpilot.agent.providers.ollama import (
    OllamaModel,
    OllamaModelError,
    SYSTEM_PROMPT,
)
from ticketpilot.agent.schemas import (
    AgentMessage,
    FinalAnswerDecision,
    ModelDecision,
    ToolCallDecision,
)


class FakeOllamaClient:
    """记录请求并返回预设内容，不连接真实 Ollama。"""

    def __init__(
        self,
        content: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, object]] = []

    def chat(self, **kwargs) -> ChatResponse:
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return ChatResponse(
            message=Message(
                role="assistant",
                content=self.content,
            ),
        )


@pytest.mark.parametrize("duration", [None, 0, 2_000_000_000])
def test_ollama_timing_does_not_change_answer(
    duration: int | None,  # 模拟未提供、零耗时和正常耗时。
    capsys: pytest.CaptureFixture[str],  # 捕获终端输出，不调用真实模型。
) -> None:
    class TimingClient(FakeOllamaClient):
        def chat(self, **kwargs) -> ChatResponse:
            response = super().chat(**kwargs)
            return response.model_copy(update={
                "total_duration": duration,
                "load_duration": duration,
                "prompt_eval_duration": duration,
                "eval_duration": duration,
                "eval_count": 10,
                "prompt_eval_count": 20,
            })

    model = OllamaModel(
        client=TimingClient('{"kind":"final_answer","answer":"测试答案"}'),
        model="test-model",
    )
    decision = model.decide([AgentMessage(role="user", content="测试问题")])
    output = capsys.readouterr().out
    assert decision.answer == "测试答案"
    assert "测试问题" not in output
    assert "测试答案" not in output
    if duration is None:
        assert "未提供" in output
    elif duration == 0:
        assert "0.00 秒" in output
    else:
        assert "2.00 秒" in output
        assert "5.00 token/秒" in output
    if not duration:
        assert "生成速度" not in output


def test_ollama_model_parses_final_answer() -> None:
    client = FakeOllamaClient(
        content=(
            '{"kind":"final_answer",'
            '"answer":"你好，我是 TicketPilot。"}'
        ),
    )
    model = OllamaModel(
        client=client,
        model="test-model",
    )

    decision = model.decide(
        [
            AgentMessage(
                role="user",
                content="你好",
            )
        ]
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert decision.answer == "你好，我是 TicketPilot。"

    request = client.calls[0]
    assert request["model"] == "test-model"
    assert request["stream"] is False
    assert request["think"] is False
    assert request["options"] == {"temperature": 0}

    sent_messages = request["messages"]
    assert isinstance(sent_messages, list)
    assert sent_messages[0]["role"] == "system"
    assert sent_messages[1] == {
        "role": "user",
        "content": "你好",
    }

    # 生成时使用完整的 ModelDecision Schema，而非仅要求合法 JSON。
    assert request["format"] == TypeAdapter(ModelDecision).json_schema()


def test_ollama_model_parses_final_answer_citations() -> None:
    """Ollama 声明的片段 ID 应保留在最终决定中。"""

    client = FakeOllamaClient(
        content=(
            '{"kind":"final_answer",'
            '"answer":"请联系管理员。",'
            '"cited_chunk_ids":["pdf-chunk-001"]}'
        ),
    )

    decision = OllamaModel(client=client).decide(
        [AgentMessage(role="user", content="如何重置密码？")]
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert decision.cited_chunk_ids == ["pdf-chunk-001"]


def test_ollama_model_validates_evidence_with_schema() -> None:
    """即使模型使用 Schema 生成，引句列表仍由 Pydantic 校验。"""

    client = FakeOllamaClient(
        content=(
            '{"kind":"final_answer",'
            '"answer":"按送货日起保修叁个月。",'
            '"cited_chunk_ids":["contract-chunk-001"],'
            '"evidence":[{"chunk_id":"contract-chunk-001",'
            '"quote":"按送货日起，非人为故障保修叁个月。"}],'
            '"evidence_is_sufficient":true}'
        ),
    )

    decision = OllamaModel(client=client).decide(
        [AgentMessage(role="user", content="保修从何时起算？")]
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert len(decision.evidence) == 1
    assert decision.evidence[0].chunk_id == "contract-chunk-001"
    assert decision.evidence[0].quote == "按送货日起，非人为故障保修叁个月。"
    assert decision.evidence_is_sufficient is True


def test_system_prompt_examples_are_valid_and_include_citations() -> None:
    """提示词中的 JSON 示例必须合法，最终回答必须展示引用字段。"""

    examples = re.findall(r"(?ms)^\{\n.*?^\}", SYSTEM_PROMPT)
    assert len(examples) == 7
    decision_adapter = TypeAdapter(ModelDecision)

    for example in examples:
        decision = json.loads(example)
        decision_adapter.validate_python(decision)
        if decision["kind"] == "final_answer":
            if decision["evidence_is_sufficient"]:
                assert decision["cited_chunk_ids"]
                assert decision["evidence"]
            else:
                assert decision["cited_chunk_ids"] == []
                assert decision["evidence"] == []

    assert "ID 必须来自已收到的 search_knowledge matches" in SYSTEM_PROMPT


def test_ollama_model_parses_tool_call() -> None:
    client = FakeOllamaClient(
        content=(
            '{"kind":"tool_call",'
            '"tool_request":{'
            '"name":"get_ticket_details",'
            '"arguments":{"ticket_id":"ticket-001"}}}'
        ),
    )
    model = OllamaModel(client=client)

    decision = model.decide(
        [
            AgentMessage(
                role="user",
                content="查询 ticket-001",
            )
        ]
    )

    assert isinstance(decision, ToolCallDecision)
    assert decision.tool_request.name == "get_ticket_details"
    assert decision.tool_request.arguments == {
        "ticket_id": "ticket-001",
    }


def test_ollama_model_uses_final_only_schema_after_search() -> None:
    """工具已执行后，Ollama 不应再获得 tool_call 的输出结构。"""

    client = FakeOllamaClient(
        content=(
            '{"kind":"final_answer",'
            '"answer":"根据当前检索结果，无法确认答案。",'
            '"evidence_is_sufficient":false}'
        ),
    )
    model = OllamaModel(client=client)

    decision = model.decide(
        [AgentMessage(role="tool", content='{"found":false}')],
        allow_tool_calls=False,
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert client.calls[0]["format"] == (
        FinalAnswerDecision.model_json_schema()
    )


def test_ollama_model_rejects_tool_call_in_final_only_mode() -> None:
    """模型违背最终回答约束时，不能把工具请求当成合法结果。"""

    client = FakeOllamaClient(
        content=(
            '{"kind":"tool_call",'
            '"tool_request":{"name":"search_knowledge",'
            '"arguments":{"query":"重复问题"}}}'
        ),
    )

    with pytest.raises(
        OllamaModelError,
        match="不符合 FinalAnswerDecision",
    ):
        OllamaModel(client=client).decide(
            [AgentMessage(role="tool", content='{"found":true}')],
            allow_tool_calls=False,
        )


def test_ollama_model_rejects_invalid_structured_output() -> None:
    client = FakeOllamaClient(
        content='{"kind":"unknown"}',
    )
    model = OllamaModel(client=client)

    with pytest.raises(
        OllamaModelError,
        match="不符合 ModelDecision",
    ):
        model.decide(
            [AgentMessage(role="user", content="你好")]
        )


def test_ollama_model_rejects_empty_response() -> None:
    client = FakeOllamaClient(content=None)
    model = OllamaModel(client=client)

    with pytest.raises(
        OllamaModelError,
        match="空响应",
    ):
        model.decide(
            [AgentMessage(role="user", content="你好")]
        )


def test_ollama_model_wraps_connection_error() -> None:
    client = FakeOllamaClient(
        error=ConnectionError("无法连接"),
    )
    model = OllamaModel(client=client)

    with pytest.raises(
        OllamaModelError,
        match="服务调用失败",
    ):
        model.decide(
            [AgentMessage(role="user", content="你好")]
        )


def test_ollama_model_wraps_timeout_error() -> None:
    client = FakeOllamaClient(
        error=ReadTimeout("模型响应超时"),
    )
    model = OllamaModel(client=client)

    with pytest.raises(
        OllamaModelError,
        match="服务调用失败",
    ):
        model.decide(
            [AgentMessage(role="user", content="你好")]
        )

# 这个测试负责验证系统提示包含两个 Agent 工具的规则。
def test_ollama_model_sends_knowledge_tool_instructions() -> None:
    # Fake Client 返回一个合法的直接回答。
    client = FakeOllamaClient(
        content=(
            '{"kind":"final_answer",'
            '"answer":"测试回答"}'
        ),
    )

    model = OllamaModel(
        client=client,
        model="test-model",
    )

    # 执行一次模型调用，让 Provider 组装完整消息。
    model.decide(
        [
            AgentMessage(
                role="user",
                content="如何重置密码？",
            ),
        ]
    )

    # 取得发送给 Ollama 的消息列表。
    sent_messages = (
        client.calls[0]["messages"]
    )

    assert isinstance(
        sent_messages,
        list,
    )

    # 第一条消息必须是系统提示。
    system_message = sent_messages[0]

    assert system_message["role"] == "system"

    system_content = system_message["content"]

    assert isinstance(
        system_content,
        str,
    )

    # 系统提示必须声明具体工单查询工具。
    assert (
        "get_ticket_details"
        in system_content
    )

    # 系统提示必须声明企业知识检索工具。
    assert (
        "search_knowledge"
        in system_content
    )

    # 必须提醒模型不能编造工单 ID。
    assert (
        "不得编造 ticket_id"
        in system_content
    )

    # 必须要求知识回答说明文档来源。
    assert (
        "document_title"
        in system_content
    )

    # 用户消息应该紧跟在系统提示后面。
    assert sent_messages[1] == {
        "role": "user",
        "content": "如何重置密码？",
    }
