"""
定义知识库 HTTP 接口使用的请求和响应 Schema。

职责：
- 验证客户端提交的标题和正文。
- 限制手动文档的最大长度。
- 定义成功入库后返回给客户端的数据。
- 不在 Schema 中执行清洗、切块或数据库操作。
"""

# datetime 用于响应中的创建和更新时间。
from datetime import datetime

# Pydantic 负责验证 HTTP 请求和响应数据。
from pydantic import (
    #ConfigDict禁止客户端偷偷提交 Schema 中没有定义的字段
    ConfigDict,
    BaseModel,
    Field,
    field_validator,
)

# 导入知识检索默认参数和最大返回数量。
from ticketpilot.knowledge.retrieval import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_RETRIEVAL_TOP_K,
    MAX_RETRIEVAL_TOP_K,
)

# 导入知识文档来源和状态枚举。
from ticketpilot.knowledge.documents import (
    KnowledgeDocumentStatus,
    KnowledgeSourceType,
)


# 表示客户端手动提交一篇知识文档的请求。
class KnowledgeDocumentCreateRequest(
    BaseModel
):
    # 禁止客户端提交 Schema 中没有定义的额外字段。
    model_config = ConfigDict(
        extra="forbid"
    )
    # 文档标题不能为空，数据库最多保存 200 个字符。
    title: str = Field(
        min_length=1,
        max_length=200,
    )

    # 当前同步入库接口最多接收 100000 个字符。
    content: str = Field(
        min_length=1,
        max_length=100_000,
    )


    # 这个函数负责拒绝只包含空格或换行的文本。
    @field_validator(
        "title",
        "content",
    )
    @classmethod
    def reject_blank_text(
        cls,
        value: str,
    ) -> str:
        if not value.strip():
            raise ValueError(
                "文本不能只包含空白字符"
            )

        # 返回原值，不删除正文两端的原始空白。
        # 真正的规范化由 Cleaning 层负责。
        return value


# 表示成功创建知识文档后的 API 响应。
class KnowledgeDocumentResponse(
    BaseModel
):
    # 文档唯一 ID。
    id: str

    # 清洗后的标题。
    title: str

    # manual、file 或 url。
    source_type: KnowledgeSourceType

    # 原始文件名或网页地址；手动输入时通常为空。
    source_uri: str | None

    # pending、processing、ready、failed 或 archived。
    status: KnowledgeDocumentStatus

    # 本次生成的知识片段数量。
    chunk_count: int = Field(
        ge=1
    )

    # 实际使用的向量模型。
    embedding_model: str

    # 向量维度，当前固定为 1024。
    embedding_dimensions: int

    # 当前文档版本。
    version: int

    # 创建这篇文档的用户 ID。
    created_by: str

    # 数据库生成的创建时间。
    created_at: datetime

    # 数据库生成的最后更新时间。
    updated_at: datetime

# 表示客户端提交一次知识检索请求。
class KnowledgeSearchRequest(
    BaseModel
):
    # 禁止客户端提交没有定义的额外字段。
    model_config = ConfigDict(
        extra="forbid"
    )

    # 用户希望从企业知识库中查询的问题。
    query: str = Field(
        min_length=1,
        max_length=2000,
    )

    # 最多返回多少个知识片段。
    top_k: int = Field(
        default=DEFAULT_RETRIEVAL_TOP_K,
        ge=1,
        le=MAX_RETRIEVAL_TOP_K,
    )

    # 只返回达到最低相似度的知识片段。
    min_similarity: float = Field(
        default=DEFAULT_MIN_SIMILARITY,
        ge=0.0,
        le=1.0,
    )

    # 这个函数负责拒绝只包含空格或换行的问题。
    @field_validator(
        "query"
    )
    @classmethod
    def reject_blank_query(
        cls,
        value: str,
    ) -> str:
        if not value.strip():
            raise ValueError(
                "查询问题不能只包含空白字符"
            )

        # Service 层还会统一执行 strip()。
        return value


# 表示 API 返回的一条相关知识片段。
class KnowledgeSearchResultResponse(
    BaseModel
):
    # 知识片段 ID。
    chunk_id: str

    # 片段所属文档 ID。
    document_id: str

    # 知识文档标题，用于显示引用来源。
    document_title: str

    # 当前知识文档版本。
    document_version: int = Field(
        ge=1
    )

    # manual、file 或 url。
    source_type: KnowledgeSourceType

    # 文件名或者网页地址。
    source_uri: str | None

    # 片段在文档中的顺序。
    chunk_index: int = Field(
        ge=0
    )

    # 真正返回给调用方的知识片段内容。
    content: str = Field(
        min_length=1
    )

    # 片段在清洗后正文中的开始位置。
    start_offset: int = Field(
        ge=0
    )

    # 片段在清洗后正文中的结束位置。
    end_offset: int = Field(
        ge=1
    )

    # 数值越大，表示片段和用户问题越相似。
    similarity: float = Field(
        ge=0.0,
        le=1.0,
    )


# 表示一次完整的知识检索响应。
class KnowledgeSearchResponse(
    BaseModel
):
    # 返回经过首尾空白清理的查询问题。
    query: str

    # 本次真正返回的结果数量。
    result_count: int = Field(
        ge=0
    )

    # 按相似度从高到低排列的知识片段。
    results: list[
        KnowledgeSearchResultResponse
    ]