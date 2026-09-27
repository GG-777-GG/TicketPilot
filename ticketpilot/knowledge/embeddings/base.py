"""
定义与具体模型厂商无关的 Embedding 接口。

文件职责：
- 定义 Embedding Provider 必须实现的方法。
- 定义批量向量生成结果。
- 定义统一的 Embedding 异常。
- 让 Ingestion Service 不直接依赖 Ollama。

正常流程：
知识片段文本列表
→ EmbeddingProvider.embed_texts()
→ EmbeddingBatch
→ Ingestion Service
→ KnowledgeChunkRecord
→ PostgreSQL pgvector

设计目的：
Ingestion Service 只依赖 EmbeddingProvider 接口。

当前可以使用：
OllamaEmbeddingProvider

以后也可以替换成：
OpenAIEmbeddingProvider
AzureEmbeddingProvider
其他企业内部模型
"""

# Sequence 表示有顺序、可以遍历的只读集合接口。
from collections.abc import Sequence

# dataclass 用来定义只负责携带数据的结果对象。
from dataclasses import dataclass

# Protocol 用来定义类似 Java interface 的接口。
from typing import Protocol


# 所有 Embedding Provider 异常的公共父类。
class EmbeddingProviderError(RuntimeError):
    """调用 Embedding 服务失败。"""


# 表示调用者传入了无效文本。
class InvalidEmbeddingInputError(ValueError):
    """Embedding 输入为空或者包含无效文本。"""


# 表示模型返回的数量、维度或数值不符合约定。
class InvalidEmbeddingResponseError(
    EmbeddingProviderError
):
    """Embedding 模型返回了无效向量。"""


# 保存一次批量 Embedding 的完整结果。
@dataclass(
    frozen=True,
    slots=True,
)
class EmbeddingBatch:
    """保存模型名称、向量维度和批量向量。"""

    # 生成这些向量所使用的模型名称。
    model_name: str

    # 每个向量包含多少个浮点数。
    dimensions: int

    # 外层 tuple 对应多段输入文本。
    # 内层 tuple 是一段文本对应的向量。
    vectors: tuple[
        tuple[float, ...],
        ...,
    ]

    # 返回本次生成的向量数量。
    @property
    def count(self) -> int:
        """返回批量结果中的向量数量。"""

        return len(
            self.vectors
        )


# 定义所有 Embedding Provider 都必须遵守的接口。
class EmbeddingProvider(Protocol):
    """把一批文本转换成一批知识向量。"""

    # 这个属性负责返回当前使用的模型名称。
    @property
    def model_name(self) -> str:
        ...

    # 这个属性负责返回模型生成的向量维度。
    @property
    def dimensions(self) -> int:
        ...

    # 这个函数负责把一批文本转换成一批向量。
    def embed_texts(
        self,
        texts: Sequence[str],
    ) -> EmbeddingBatch:
        ...