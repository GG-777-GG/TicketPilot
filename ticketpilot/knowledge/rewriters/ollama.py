"""调用 Ollama，把用户问题改写成适合知识库检索的问题。"""

import logging

from httpx import RequestError as HttpxRequestError
from ollama import Client, RequestError as OllamaRequestError, ResponseError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ticketpilot.core.config import settings


logger = logging.getLogger(__name__)

_REWRITE_PROMPT = """
你负责改写企业知识库的检索问题，不负责回答。
只生成一条更清晰、可独立理解的检索问题。
保留原问题中的人名、文档名、数字、时间、否定和限制条件。
不得添加原问题没有提出的事实。
如果原问题已经清楚，可以原样返回。
只按提供的 JSON Schema 输出 query。
""".strip()


class _RewriteResponse(BaseModel):
    """约束 Ollama 返回的数据格式。"""

    # 自动清除结果两端的空格，并拒绝多余字段。
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    # 空问题或过长的问题都视为无效改写。
    query: str = Field(min_length=1, max_length=300)


class OllamaQueryRewriter:
    """使用本地 Ollama 实现问题重写。"""

    def __init__(
        self,
        *,
        # 测试时可以传入 Fake Client，不必启动真实 Ollama。
        client: Client | None = None,
        model: str | None = None,
    ) -> None:
        self._client = client if client is not None else Client(
            host=settings.ollama_base_url,
            timeout=settings.ollama_timeout_seconds,
        )
        self._model = model or settings.ollama_model

    def rewrite(self, question: str) -> str | None:
        """生成检索问题；服务失败时返回 None，让上层使用原问题。"""

        original = question.strip()
        if not original:
            return None

        try:
            response = self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": _REWRITE_PROMPT},
                    {"role": "user", "content": original},
                ],
                stream=False,
                think=False,
                # 让模型按 _RewriteResponse 的字段生成 JSON。
                format=_RewriteResponse.model_json_schema(),
                options={"temperature": 0},
            )
        except (
            ConnectionError,
            HttpxRequestError,
            OllamaRequestError,
            ResponseError,
        ):
            logger.warning("问题重写服务不可用，回退到原问题", exc_info=True)
            return None

        content = response.message.content
        if not content:
            return None

        try:
            rewritten = _RewriteResponse.model_validate_json(content).query
        except ValidationError:
            logger.warning("问题重写结果格式无效，回退到原问题")
            return None

        # 没有变化就不必再次检索相同的问题。
        return None if rewritten == original else rewritten