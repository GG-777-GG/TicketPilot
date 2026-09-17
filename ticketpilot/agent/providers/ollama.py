"""把本地 Ollama 模型适配成 TicketPilot 的 AgentModel 接口。"""

from httpx import TimeoutException
from ollama import (
    Client,
    RequestError,
    ResponseError,
)
from pydantic import (
    TypeAdapter,
    ValidationError,
)

from ticketpilot.agent.schemas import (
    AgentMessage,
    ModelDecision,
)
from ticketpilot.core.config import settings


# TypeAdapter 让 Pydantic 可以验证“两个模型组成的联合类型”。
# ModelDecision 可能是 final_answer，也可能是 tool_call。
_decision_adapter = TypeAdapter(ModelDecision)


# 这段系统提示告诉真实模型它在 TicketPilot 中负责什么。
# JSON 的精确结构由下面传给 Ollama 的 JSON Schema 继续约束。
SYSTEM_PROMPT = """
你是 TicketPilot 工单助手。

你必须严格按照下面的顺序决定下一步：

1. 只有同时满足下面两个条件时，才能调用 get_ticket_details：
   - 用户明确要求查询某一张具体工单；
   - 用户明确提供了该工单的真实 ticket_id。

2. 如果用户没有提供 ticket_id，不得调用工具。
   你应该使用 final_answer，请用户提供工单 ID。

3. 不得猜测、生成或编造 ticket_id。

4. 如果用户只是打招呼、询问你的身份、询问你的能力，
   或者明确表示“不需要查询工单”，必须使用 final_answer。

5. 收到 role=tool 的消息后，必须根据工具返回的数据回答，
   不得再次编造工单信息。

你只能返回下面两种决定之一：

- 能够直接回答时，返回 final_answer。
- 确实需要查询具体工单时，返回 tool_call，
  工具名称只能是 get_ticket_details。

示例一：

用户：请用一句话介绍你自己，不需要查询工单

决定：
{
  "kind": "final_answer",
  "answer": "我是 TicketPilot 工单助手，可以帮助你查询和理解工单信息。"
}

示例二：

用户：帮我查询工单 ticket-001

决定：
{
  "kind": "tool_call",
  "tool_request": {
    "name": "get_ticket_details",
    "arguments": {
      "ticket_id": "ticket-001"
    }
  }
}

示例三：

用户：帮我查询一张工单

决定：
{
  "kind": "final_answer",
  "answer": "请提供需要查询的工单 ID。"
}

只返回符合所提供 JSON Schema 的 JSON。
不要添加 Markdown 代码块或其他说明。
""".strip()


class OllamaModelError(RuntimeError):
    """表示 Ollama 调用失败，或者模型返回了不符合约定的数据。"""


class OllamaModel:
    """使用 Ollama 的结构化输出实现 AgentModel 的 decide()。"""

    def __init__(
        self,
        *,
        client: Client | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        # 测试可以传入 Fake Client；正式运行时才创建真实 Ollama Client。
        self._client = client or Client(
            host=base_url or settings.ollama_base_url,
            timeout=(
                timeout_seconds
                if timeout_seconds is not None
                else settings.ollama_timeout_seconds
            ),
        )

        # 没有显式指定模型时，使用环境配置中的默认模型。
        self._model = model or settings.ollama_model

    def decide(
        self,
        messages: list[AgentMessage],
    ) -> ModelDecision:
        """把消息发给 Ollama，并验证它返回的下一步决定。"""

        # Provider 自己补充系统提示，Agent Loop 不需要依赖某个具体模型。
        ollama_messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            *[
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in messages
            ],
        ]

        try:
            response = self._client.chat(
                model=self._model,
                messages=ollama_messages,
                # 本阶段使用完整响应，不处理流式 token。
                stream=False,
                # 不需要模型返回思考过程，只需要结构化决定。
                think=False,
                # 把 ModelDecision 的 JSON Schema 交给 Ollama 约束输出。
                format=_decision_adapter.json_schema(),
                # 降低随机性，让工具选择和自动测试更稳定。
                options={"temperature": 0},
            )
        except (
            ConnectionError,
            RequestError,
            ResponseError,
            TimeoutException,
        ) as error:
            raise OllamaModelError(
                "Ollama 服务调用失败",
            ) from error

        content = response.message.content
        if not content:
            raise OllamaModelError(
                "Ollama 返回了空响应",
            )

        try:
            # validate_json 同时完成 JSON 解析和 ModelDecision 字段验证。
            return _decision_adapter.validate_json(
                content,
            )
        except ValidationError as error:
            raise OllamaModelError(
                "Ollama 返回的数据不符合 ModelDecision",
            ) from error
