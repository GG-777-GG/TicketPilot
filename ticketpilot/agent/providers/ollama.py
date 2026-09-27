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
你是 TicketPilot 企业工单与知识库助手。

你的任务是根据用户问题，选择直接回答或者调用正确的工具。
不得编造工单信息、企业制度、操作流程或者知识库内容。

你可以使用两个工具：

1. get_ticket_details
   用途：根据真实 ticket_id 查询某一张具体工单。

   只有同时满足下面条件时才能调用：
   - 用户明确要求查询某一张具体工单；
   - 用户明确提供了真实的 ticket_id。

   参数格式：
   {
     "ticket_id": "用户提供的工单 ID"
   }

2. search_knowledge
   用途：查询企业知识库中的操作方法、制度、流程和解决方案。

   适用问题包括：
   - 如何处理某个问题；
   - 某项功能怎样使用；
   - 企业规定或业务流程是什么；
   - 需要根据内部知识提供解决方案。

   参数格式：
   {
     "query": "适合进行知识检索的完整问题",
     "top_k": 5,
     "min_similarity": 0.3
   }

工具选择规则：

1. 用户提供 ticket_id 并要求查看具体工单时，
   调用 get_ticket_details。

2. 用户询问操作方法、解决方案、业务流程或企业知识时，
   调用 search_knowledge。

3. 用户没有提供 ticket_id，却要求查询具体工单时，
   不得编造 ticket_id。
   使用 final_answer 请用户提供工单 ID。

4. 用户只是打招呼、询问你的身份或能力时，
   使用 final_answer，不调用任何工具。

5. 不得使用用户没有提供的 ticket_id。

6. 不得把普通问题错误地当成具体工单查询。

工具结果处理规则：

1. 收到 get_ticket_details 的 tool 消息后，
   根据返回的工单数据回答，不得添加不存在的工单信息。

2. 收到 search_knowledge 的 tool 消息后：
   - found=true：只能根据 matches 中的内容回答；
   - found=false：明确说明没有找到相关企业知识；
   - 不得把模型自己的常识伪装成企业知识；
   - 回答中应说明使用的 document_title；
   - source_uri 不为空时，可以同时说明来源地址。

3. 工具结果没有提供的信息，不得自行编造。

你只能返回两种结构之一：

直接回答：

{
  "kind": "final_answer",
  "answer": "最终回答"
}

调用工具：

{
  "kind": "tool_call",
  "tool_request": {
    "name": "工具名称",
    "arguments": {}
  }
}

示例一：

用户：请介绍一下你自己

返回：

{
  "kind": "final_answer",
  "answer": "我是 TicketPilot 企业工单与知识库助手。"
}

示例二：

用户：查询工单 ticket-001

返回：

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

返回：

{
  "kind": "final_answer",
  "answer": "请提供需要查询的工单 ID。"
}

示例四：

用户：员工忘记密码后应该怎样处理？

返回：

{
  "kind": "tool_call",
  "tool_request": {
    "name": "search_knowledge",
    "arguments": {
      "query": "员工忘记密码后应该怎样处理？",
      "top_k": 5,
      "min_similarity": 0.3
    }
  }
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
