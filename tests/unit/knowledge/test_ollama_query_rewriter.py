"""Ollama 问题重写器的单元测试，不连接真实模型。"""

from httpx import ReadTimeout
from ollama import ChatResponse, Message

from ticketpilot.knowledge.rewriters.ollama import OllamaQueryRewriter


class FakeOllamaClient:
    """记录 chat() 入参，并返回预设的模型结果。"""

    def __init__(
        self,
        *,
        content: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, object]] = []

    def chat(self, **kwargs: object) -> ChatResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return ChatResponse(
            message=Message(role="assistant", content=self.content),
        )


def test_rewriter_returns_valid_query_and_uses_json_schema() -> None:
    client = FakeOllamaClient(
        content='{"query": "  完成新功能的第一步是什么？  "}',
    )
    rewriter = OllamaQueryRewriter(client=client, model="test-model")

    rewritten = rewriter.rewrite("  开发新功能前应确定什么？  ")

    assert rewritten == "完成新功能的第一步是什么？"
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["model"] == "test-model"
    assert request["stream"] is False
    assert request["think"] is False
    assert request["options"] == {"temperature": 0}
    assert request["messages"][1] == {
        "role": "user",
        "content": "开发新功能前应确定什么？",
    }
    assert "query" in request["format"]["properties"]


def test_rewriter_ignores_unchanged_or_blank_input() -> None:
    client = FakeOllamaClient(content='{"query": "  原问题  "}')
    rewriter = OllamaQueryRewriter(client=client)

    assert rewriter.rewrite("原问题") is None
    assert rewriter.rewrite("  ") is None
    # 空输入不应发送到 Ollama。
    assert len(client.calls) == 1


def test_rewriter_falls_back_on_invalid_model_output() -> None:
    client = FakeOllamaClient(content='{"query":"改写","extra":"多余字段"}')
    rewriter = OllamaQueryRewriter(client=client)

    # extra="forbid" 拒绝模型生成未约定字段。
    assert rewriter.rewrite("原问题") is None


def test_rewriter_falls_back_on_empty_model_output() -> None:
    client = FakeOllamaClient(content=None)

    assert OllamaQueryRewriter(client=client).rewrite("原问题") is None


def test_rewriter_falls_back_when_ollama_times_out() -> None:
    client = FakeOllamaClient(error=ReadTimeout("测试超时"))

    assert OllamaQueryRewriter(client=client).rewrite("原问题") is None
