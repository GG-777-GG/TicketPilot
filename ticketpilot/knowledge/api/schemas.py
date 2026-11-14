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
from ticketpilot.knowledge.application.retrieval import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_RETRIEVAL_TOP_K,
    MAX_RETRIEVAL_TOP_K,
)

# 导入知识文档来源和状态枚举。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
    KnowledgeSourceType,
    KnowledgeProcessingStage,
    KnowledgeIngestionJobStatus
)

from typing import Literal


# 表示客户端手动提交一篇知识文档的请求。
class _KnowledgeDocumentContentRequest(
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

# 创建文档时确定可见性，默认保持原有 internal 行为。
class KnowledgeDocumentCreateRequest(
    _KnowledgeDocumentContentRequest
):
    visibility: Literal["internal", "restricted"] = "internal"

# 这个类定义客户端查询入库任务时能看到哪些字段。
class KnowledgeIngestionJobResponse(BaseModel):
    # 允许 Pydantic 从 ORM 对象的属性读取字段。
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source_type: KnowledgeSourceType
    source_uri: str | None
    visibility: Literal["internal", "restricted"]
    requested_by: str
    status: KnowledgeIngestionJobStatus
    attempt_count: int
    document_id: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    # 当前执行阶段；尚未上报阶段时明确返回 null。
    processing_stage: KnowledgeProcessingStage | None

class KnowledgeIngestionJobListResponse(BaseModel):
    """定义任务列表接口返回的数据结构。

    items：当前页允许用户查看的任务。
    total：当前用户查询范围内的任务总数。
    limit：本次请求的每页数量上限。
    offset：本次查询跳过的任务数量。

    只负责响应数据的结构和校验，不执行数据库查询。
    """

    # 每一条任务都沿用已有的公开响应结构。
    # 不直接暴露 ORM 中的文件存储路径、租约令牌等内部字段。
    items: list[KnowledgeIngestionJobResponse]

    # 符合查询条件的总数，不能为负数。
    total: int = Field(ge=0)

    # 本次请求的每页数量上限，与 Repository 限制一致。
    limit: int = Field(ge=1, le=100)

    # 本次跳过多少条，不能为负数。
    offset: int = Field(ge=0)

# 表示客户端更新一篇知识文档时提交的请求。
# 更新正文复用标题和正文校验，但不能修改可见性。
class KnowledgeDocumentUpdateRequest(
    _KnowledgeDocumentContentRequest
):
    """
    更新知识文档的请求数据。

    title 和 content 表示完整的新版本内容。
    expected_version 用于防止并发更新互相覆盖。
    """

    # 客户端读取文档时看到的版本号。
    #
    # 更新时，Service 会把它与数据库当前版本比较。
    expected_version: int = Field(
        # 文档版本号必须从 1 开始。
        ge=1,
    )

# 这个请求只允许修改标题，不接收正文。
class KnowledgeDocumentTitleUpdateRequest(BaseModel):
    model_config = ConfigDict(
        # 拒绝误传 content 等未定义字段。
        extra="forbid",

        # 自动去掉标题两端的空格。
        str_strip_whitespace=True,
    )

    # 新标题不能为空，最长 200 个字符。
    title: str = Field(
        min_length=1,
        max_length=200,
    )

    # 防止覆盖别人刚更新的版本。
    expected_version: int = Field(
        ge=1,
    )

# 表示客户端请求恢复一个历史版本。
class KnowledgeDocumentVersionRestoreRequest(
    BaseModel
):
    # 禁止客户端提交未定义的额外字段。
    model_config = ConfigDict(
        extra="forbid"
    )

    # 客户端读取当前文档时看到的版本号。
    #
    # 它不是要恢复的历史版本号；
    # 要恢复的历史版本号来自 URL 路径。
    expected_version: int = Field(
        ge=1
    )


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

    # 管理端需要知道这篇文档的可见范围。
    # API 响应必须明确给出真实可见性，不能猜默认值。
    visibility: Literal["internal", "restricted"]

    # 本次生成的知识片段数量。
    chunk_count: int = Field(
        ge=0
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


# 表示分页查询知识文档列表的 API 响应。
class KnowledgeDocumentListResponse(
    BaseModel
):
    # 当前页面中的知识文档。
    items: list[
        KnowledgeDocumentResponse
    ]

    # 数据库中的全部知识文档数量。
    total: int = Field(
        ge=0
    )

    # 当前请求允许返回的最大数量。
    limit: int = Field(
        ge=1,
        le=100,
    )

    # 当前页面跳过了前面多少条数据。
    offset: int = Field(
        ge=0
    )

# 表示查询单篇知识文档详情时的 API 响应。
class KnowledgeDocumentDetailResponse(
    KnowledgeDocumentResponse
):
    # 用户最初提交的原始正文。
    raw_content: str

    # 经过 Cleaning 层处理后的正文。
    cleaned_content: str

    # 当前正在执行的处理阶段。
    # 文档处理完成后通常为 None。
    processing_stage: (
        KnowledgeProcessingStage
        | None
    )

    # 生成 cleaned_content 的清洗器版本。
    cleaner_version: str

    # 生成知识片段的切块器版本。
    chunker_version: str

# 表示历史版本列表中的一条版本摘要。
class KnowledgeDocumentVersionResponse(
    BaseModel
):
    # 历史版本号，例如 1、2、3。
    version: int = Field(
        ge=1
    )

    # 这个版本当时使用的标题。
    title: str

    # 这个版本当时的来源类型。
    source_type: KnowledgeSourceType

    # 这个版本当时的文件地址或网页地址。
    source_uri: str | None

    # 这个版本当时使用的向量模型。
    embedding_model: str

    # 这个版本当时的向量维度。
    embedding_dimensions: int

    # 这个版本当时使用的清洗器版本。
    cleaner_version: str

    # 这个版本当时使用的切块器版本。
    chunker_version: str

    # 最初创建这个内容版本的用户 ID。
    created_by: str

    # 这个内容版本最初创建的时间。
    created_at: datetime

    # 后来由哪个用户替换了这个版本。
    superseded_by: str

    # 这个版本被替换的时间。
    superseded_at: datetime

# 表示某一个历史版本的完整详情响应。
#
# 它继承历史版本摘要中的全部公共字段，
# 再增加原始正文和清洗后正文。
class KnowledgeDocumentVersionDetailResponse(
    KnowledgeDocumentVersionResponse
):
    # 用户当时提交的原始正文。
    raw_content: str

    # 经过当时 Cleaning 层处理后的正文。
    cleaned_content: str


# 表示分页查询历史版本列表的 API 响应。
class KnowledgeDocumentVersionListResponse(
    BaseModel
):
    # 当前查询的是哪一篇知识文档。
    document_id: str

    # 当前文档正在使用的版本号。
    current_version: int = Field(
        ge=1
    )

    # 当前页面中的历史版本。
    items: list[
        KnowledgeDocumentVersionResponse
    ]

    # 这篇文档拥有的历史版本总数。
    total: int = Field(
        ge=0
    )

    # 当前请求最多允许返回多少条。
    limit: int = Field(
        ge=1,
        le=100,
    )

    # 当前页面跳过了多少条历史版本。
    offset: int = Field(
        ge=0
    )

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

    # 片段对应的 PDF 原始页码；非 PDF 文档返回空列表。
    page_numbers: list[int]

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
