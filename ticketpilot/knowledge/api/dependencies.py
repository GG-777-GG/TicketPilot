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

# 导入知识文件上传 Service。
from ticketpilot.knowledge.application.uploads import (
    KnowledgeFileUploadService,
)

# 读取上传文件的存储目录配置。
from ticketpilot.core.config import settings

# 本地原始文件存储服务。
from ticketpilot.knowledge.persistence.file_storage import (
    LocalKnowledgeFileStorage,
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

# 这个函数负责创建并缓存知识文件上传 Service。
@lru_cache
def get_knowledge_file_upload_service(
) -> KnowledgeFileUploadService:
    return KnowledgeFileUploadService()


# 定义可以直接用于 FastAPI 路由参数的上传 Service 依赖。
KnowledgeFileUploadServiceDependency = Annotated[
    # 路由最终获得知识文件上传 Service。
    KnowledgeFileUploadService,

    # FastAPI 自动调用创建函数。
    Depends(
        get_knowledge_file_upload_service
    ),
]

def get_knowledge_file_storage() -> LocalKnowledgeFileStorage:
    """创建知识文件存储服务。

    正常流程：
        读取配置中的上传目录
        → 创建本地文件存储对象
        → 返回给需要它的路由。

    异常处理：
        路径初始化异常继续向上传递。

    返回：
        使用服务端配置目录的 LocalKnowledgeFileStorage 对象。

    调用约定：
        此处只创建对象；实际保存文件时才创建目录。
        Worker 必须使用同一个目录配置。
    """
    return LocalKnowledgeFileStorage(
        # 使用服务端配置，不能由用户请求指定存储目录。
        directory=settings.knowledge_upload_directory,
    )


# 定义可直接用在 Router 参数中的依赖类型。
KnowledgeFileStorageDependency = Annotated[
    # 路由实际拿到的对象类型。
    LocalKnowledgeFileStorage,

    # 告诉 FastAPI 调用上面的函数取得对象。
    Depends(get_knowledge_file_storage),
]