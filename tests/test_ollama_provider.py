import pytest
from httpx import ReadTimeout
from ollama import ChatResponse, Message

from ticketpilot.agent.providers.ollama import (
    OllamaModel,
    OllamaModelError,
)
from ticketpilot.agent.schemas import (
    AgentMessage,
    FinalAnswerDecision,
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

    output_schema = request["format"]
    assert isinstance(output_schema, dict)
    assert "anyOf" in output_schema


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
