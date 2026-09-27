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