"""
使用本地 Ollama 生成知识向量。

文件职责：
- 创建 Ollama Client。
- 读取 Embedding 模型配置。
- 验证输入文本是否合法。
- 分批调用 Ollama 的 embed()。
- 验证模型返回的向量。
- 返回统一的 EmbeddingBatch。

完整流程：
知识片段文本
→ 检查输入
→ 按 batch_size 分批
→ Ollama Client.embed()
→ 检查向量数量和维度
→ EmbeddingBatch
→ 知识入库服务
"""

# Sequence 表示有顺序的文本集合。
from collections.abc import Sequence

# isfinite 用来检查向量中是否出现 NaN 或无限大。
from math import isfinite

# Client 是 Ollama 官方 Python 客户端。
from ollama import (
    Client,
    RequestError as OllamaRequestError,
    ResponseError,
)

# 导入项目环境配置。
from ticketpilot.core.config import settings

# 导入数据库要求的固定向量维度。
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入 Embedding 输入异常。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProviderError,
    InvalidEmbeddingInputError,
    InvalidEmbeddingResponseError,
)
# HttpxRequestError 表示连接失败、超时等 HTTP 请求错误。
from httpx import (
    RequestError as HttpxRequestError,
)


# 使用本地 Ollama 实现文本转向量功能。
class OllamaEmbeddingProvider:
    """通过 Ollama 的 Embedding 模型生成知识向量。"""

    # 这个函数负责初始化 Ollama 客户端和 Embedding 配置。
    def __init__(
        self,
        *,
        # 测试时可以传入假的 Client。
        client: Client | None = None,

        # 允许调用者临时指定模型。
        model: str | None = None,

        # 允许调用者临时指定 Ollama 地址。
        base_url: str | None = None,

        # 允许调用者临时指定超时时间。
        timeout_seconds: float | None = None,

        # 允许调用者临时指定每批文本数量。
        batch_size: int | None = None,
    ) -> None:
        # 优先使用调用者传入的模型，
        # 没有传入时使用环境配置。
        selected_model = (
            model
            if model is not None
            else settings.ollama_embedding_model
        )

        # 模型名称不能是空字符串。
        if not selected_model.strip():
            raise ValueError(
                "Embedding 模型名称不能为空"
            )

        # 选择 Ollama 服务地址。
        selected_base_url = (
            base_url
            if base_url is not None
            else settings.ollama_base_url
        )

        # Ollama 服务地址不能是空字符串。
        if not selected_base_url.strip():
            raise ValueError(
                "Ollama 服务地址不能为空"
            )

        # 选择请求超时时间。
        selected_timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else (
                settings
                .ollama_embedding_timeout_seconds
            )
        )

        # 超时时间必须大于 0。
        if selected_timeout_seconds <= 0:
            raise ValueError(
                "Embedding 超时时间必须大于 0"
            )

        # 选择批量处理数量。
        selected_batch_size = (
            batch_size
            if batch_size is not None
            else settings.embedding_batch_size
        )

        # 每批至少包含一段文本。
        if selected_batch_size < 1:
            raise ValueError(
                "Embedding 批大小必须大于 0"
            )

        # 防止一次发送过多文本占用大量内存。
        if selected_batch_size > 256:
            raise ValueError(
                "Embedding 批大小不能超过 256"
            )

        # 测试传入 Fake Client 时直接使用。
        # 正式运行时才创建真实 Ollama Client。
        self._client = (
            client
            if client is not None
            else Client(
                host=selected_base_url,
                timeout=selected_timeout_seconds,
            )
        )

        # 保存当前使用的模型名称。
        self._model = selected_model

        # 保存每一批最多处理的文本数量。
        self._batch_size = selected_batch_size

        # 向量维度必须与数据库 Vector(1024) 一致。
        self._dimensions = EMBEDDING_DIMENSIONS

    # 这个属性负责返回当前使用的模型名称。
    @property
    def model_name(self) -> str:
        return self._model

    # 这个属性负责返回模型生成的向量维度。
    @property
    def dimensions(self) -> int:
        return self._dimensions

    # 这个函数负责检查传入的文本集合是否合法。
    def _validate_texts(
        self,
        texts: Sequence[str],
    ) -> list[str]:
        # 单个字符串本身也是 Sequence，
        # 但我们需要的是“多段文本组成的集合”。
        if isinstance(
            texts,
            str,
        ):
            raise InvalidEmbeddingInputError(
                "必须传入文本集合，不能直接传入单个字符串"
            )

        # 转换成 list，方便后面进行分批切片。
        try:
            normalized_texts = list(
                texts
            )

        # 防御运行时传入 None 等不可遍历对象。
        except TypeError as error:
            raise InvalidEmbeddingInputError(
                "Embedding 输入必须是文本集合"
            ) from error

        # 至少需要一段文本。
        if not normalized_texts:
            raise InvalidEmbeddingInputError(
                "Embedding 输入不能为空"
            )

        # 逐段检查文本。
        for text_index, text in enumerate(
            normalized_texts
        ):
            # 即使类型注解要求 str，
            # 运行时仍然可能收到其他类型。
            if not isinstance(
                text,
                str,
            ):
                raise InvalidEmbeddingInputError(
                    f"第 {text_index} 段输入不是字符串"
                )

            # 空字符串和只包含空格的文本不能生成有效向量。
            if not text.strip():
                raise InvalidEmbeddingInputError(
                    f"第 {text_index} 段输入不能为空"
                )

        # 返回已经确认合法的文本列表。
        return normalized_texts
    # 这个函数负责检查模型返回的向量数量、维度和数值。
    def _validate_vectors(
        self,
        vectors: Sequence[
            Sequence[float]
        ],
        *,
        expected_count: int,
    ) -> tuple[
        tuple[float, ...],
        ...,
    ]:
        # 转换成 list，方便检查返回数量。
        try:
            received_vectors = list(
                vectors
            )

        # 如果返回值不是可以遍历的向量集合，
        # 就说明 Ollama 返回结构不正确。
        except TypeError as error:
            raise InvalidEmbeddingResponseError(
                "Embedding 返回值不是向量集合"
            ) from error

        # 一段输入文本必须对应一个向量。
        if (
            len(received_vectors)
            != expected_count
        ):
            raise InvalidEmbeddingResponseError(
                "Embedding 返回的向量数量不正确"
            )

        # 保存验证并转换完成的向量。
        normalized_vectors: list[
            tuple[float, ...]
        ] = []

        # 逐个检查返回的向量。
        for vector_index, vector in enumerate(
            received_vectors
        ):
            # 把向量中的数值统一转换成 float。
            try:
                normalized_vector = tuple(
                    float(value)
                    for value in vector
                )

            # 无法转换成 float 表示返回了非法数据。
            except (
                TypeError,
                ValueError,
                OverflowError,
            ) as error:
                raise InvalidEmbeddingResponseError(
                    f"第 {vector_index} 个向量包含非法数值"
                ) from error

            # 每个向量必须正好包含 1024 个浮点数。
            if (
                len(normalized_vector)
                != self._dimensions
            ):
                raise InvalidEmbeddingResponseError(
                    f"第 {vector_index} 个向量维度不正确"
                )

            # NaN、正无穷和负无穷都不能保存或参与检索。
            if not all(
                isfinite(value)
                for value in normalized_vector
            ):
                raise InvalidEmbeddingResponseError(
                    f"第 {vector_index} 个向量包含非有限数值"
                )

            # 全零向量没有方向，无法进行有效的余弦相似度检索。
            if not any(
                value != 0.0
                for value in normalized_vector
            ):
                raise InvalidEmbeddingResponseError(
                    f"第 {vector_index} 个向量不能全部为 0"
                )

            # 当前向量验证通过，加入最终结果。
            normalized_vectors.append(
                normalized_vector
            )

        # 返回不可修改的批量向量。
        return tuple(
            normalized_vectors
        )

    # 这个函数负责分批调用 Ollama，把文本转换成向量。
    def embed_texts(
        self,
        texts: Sequence[str],
    ) -> EmbeddingBatch:
        # 首先验证所有输入文本。
        validated_texts = self._validate_texts(
            texts
        )

        # 保存所有批次最终生成的向量。
        all_vectors: list[
            tuple[float, ...]
        ] = []

        # 根据 batch_size 分批处理文本。
        for batch_start in range(
            0,
            len(validated_texts),
            self._batch_size,
        ):
            # 提取当前批次的文本。
            current_batch = validated_texts[
                batch_start:
                batch_start + self._batch_size
            ]

            # 捕获 Ollama 或底层 HTTP 请求异常。
            try:
                # embed 的意思是：
                # 把当前批次文本转换成向量。
                response = self._client.embed(
                    model=self._model,
                    input=current_batch,

                    # 超长内容不允许被模型静默截断。
                    # 如果切块过长，应该暴露问题并修正切块器。
                    truncate=False,

                    # 明确要求返回数据库需要的 1024 维向量。
                    dimensions=self._dimensions,
                )

            # 只捕获已知的外部服务调用异常。
            except (
                ConnectionError,
                HttpxRequestError,
                OllamaRequestError,
                ResponseError,
            ) as error:
                raise EmbeddingProviderError(
                    "Ollama Embedding 服务调用失败"
                ) from error

            # 正常的 Ollama 响应应该包含 embeddings 字段。
            try:
                response_vectors = (
                    response.embeddings
                )

            # 没有 embeddings 字段说明响应结构不正确。
            except AttributeError as error:
                raise InvalidEmbeddingResponseError(
                    "Ollama 响应缺少 embeddings 字段"
                ) from error

            # 检查当前批次的向量数量、维度和数值。
            validated_vectors = (
                self._validate_vectors(
                    response_vectors,
                    expected_count=len(
                        current_batch
                    ),
                )
            )

            # 按照批次顺序加入最终结果。
            all_vectors.extend(
                validated_vectors
            )

        # 返回统一且不可修改的批量结果。
        return EmbeddingBatch(
            model_name=self._model,
            dimensions=self._dimensions,
            vectors=tuple(
                all_vectors
            ),
        )