"""
定义知识库模块使用的 FastAPI 依赖。

职责：
- 创建真实的 Ollama Embedding Provider。
- 缓存可重复使用的 Provider 对象。
- 允许自动化测试替换成 Fake Provider。
"""

# lru_cache 用来缓存已经创建的 Provider 对象。
from functools import lru_cache

# Annotated 用来组合类型与 FastAPI 依赖信息。
from typing import Annotated

# Depends 表示对象由 FastAPI 自动提供。
from fastapi import Depends

# 导入所有向量模型必须遵守的统一接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

# 导入真正连接本地 Ollama 的实现。
from ticketpilot.knowledge.embeddings.ollama import (
    OllamaEmbeddingProvider,
)


# 这个函数负责创建并缓存知识库使用的向量模型。
@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    # 第一次调用时创建真实 Provider，
    # 后续请求会复用同一个对象。
    return OllamaEmbeddingProvider()


# 定义可以直接用于 FastAPI 路由参数的依赖类型。
EmbeddingProviderDependency = Annotated[
    # 路由最终得到符合统一接口的 Provider。
    EmbeddingProvider,

    # FastAPI 自动调用 get_embedding_provider()。
    Depends(
        get_embedding_provider
    ),
]