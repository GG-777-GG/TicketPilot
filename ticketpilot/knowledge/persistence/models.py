"""
定义知识库模块的 SQLAlchemy ORM 模型。

文件职责：
- 把清洗后的知识文档映射到 knowledge_documents 表。
- 把知识片段和向量映射到 knowledge_chunks 表。
- 建立文档与片段之间的一对多关系。
- 通过数据库约束保护数据完整性。
- 同时兼容 PostgreSQL 和测试使用的 SQLite。

正常入库流程：
RawDocument
→ 文档清洗
→ 文本切块
→ 生成 Embedding
→ 创建 KnowledgeDocumentRecord
→ 创建多个 KnowledgeChunkRecord
→ Session 提交事务
→ 文档状态变成 ready

异常流程：
- 文档状态、来源类型不合法时，数据库拒绝保存。
- 向量维度不是 1024 时，PostgreSQL 拒绝保存。
- chunk_index、offset 不合法时，数据库拒绝保存。
- created_by 对应的用户不存在时，数据库拒绝保存。
- 任意数据库操作失败时，由 Service 执行 rollback。

删除流程：
删除 KnowledgeDocumentRecord
→ ORM 删除所属 KnowledgeChunkRecord
→ 数据库 ON DELETE CASCADE 提供第二层保护

SQLite 测试兼容：
PostgreSQL
→ embedding 使用 Vector(1024)

SQLite
→ embedding 使用 JSON 保存浮点数列表

后续重构：
- 增加独立的 DocumentVersion 表。
- 增加 IngestionJob 后台任务表。
- 增加 tenant_id 和文档 ACL。
- 增加页码、章节、token_count 和结构化 metadata。
- 增加 HNSW 向量索引。

完整调用链：
Knowledge Service
→ Knowledge Repository
→ SQLAlchemy Session
→ KnowledgeDocumentRecord
→ KnowledgeChunkRecord
→ PostgreSQL + pgvector
"""

# 推迟类型注解求值。
# 这样 Document 类可以引用后面才定义的 Chunk 类。
from __future__ import annotations

# datetime 用于 ORM 时间字段的 Python 类型注解。
from datetime import datetime

# Vector 是 pgvector 提供的 SQLAlchemy 向量字段类型。
from pgvector.sqlalchemy import Vector

# 导入数据库字段类型、约束和 SQL 函数。
from sqlalchemy import (
    # CheckConstraint 用数据库规则限制字段值。
    CheckConstraint,

    # DateTime 保存创建时间和更新时间。
    DateTime,

    # ForeignKey 定义表之间的外键关系。
    ForeignKey,

    # Integer 保存版本号、维度和片段顺序。
    Integer,

    # JSON 用作 SQLite 测试中的向量字段类型。
    JSON,

    # String 保存有长度限制的文本字段。
    String,

    # Text 保存可能很长的正文和错误信息。
    Text,

    # UniqueConstraint 定义组合唯一约束。
    UniqueConstraint,

    # func 提供数据库的 now() 等函数。
    func,
)

# 导入 SQLAlchemy 2.x ORM 字段和关系工具。
from sqlalchemy.orm import (
    # Mapped 描述 ORM 属性对应的 Python 类型。
    Mapped,

    # mapped_column 定义实际数据库字段。
    mapped_column,

    # relationship 建立 ORM 对象之间的关系。
    relationship,
)

# 导入项目统一的 ORM 基类。
from ticketpilot.core.database import Base

# 导入知识向量固定维度。
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入知识文档的来源和状态枚举。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
    KnowledgeProcessingStage,
    KnowledgeSourceType,
    KnowledgeIngestionJobStatus
)


# PostgreSQL 使用真正的 pgvector Vector(1024)。
#
# with_variant() 表示：
# - PostgreSQL 继续使用 Vector。
# - SQLite 改用 JSON。
#
# 这样现有 SQLite 自动化测试不需要安装 pgvector 扩展。
_EMBEDDING_COLUMN_TYPE = Vector(
    EMBEDDING_DIMENSIONS
).with_variant(
    JSON(),
    "sqlite",
)


# 表示数据库中的一篇知识文档。
class KnowledgeDocumentRecord(Base):
    """数据库 knowledge_documents 表中的知识文档。"""

    # 指定数据库表名。
    __tablename__ = "knowledge_documents"

    # 定义表级数据库约束。
    __table_args__ = (
        # source_type 只能使用当前支持的来源类型。
        CheckConstraint(
            (
                "source_type IN "
                "('manual', 'file', 'url')"
            ),
            name=(
                "ck_knowledge_documents_"
                "source_type"
            ),
        ),

        # 文档可见性只能是内部或受限。
        CheckConstraint(
            "visibility IN ('internal', 'restricted')",
            name="ck_knowledge_documents_visibility",
        ),

        # status 只能使用定义好的生命周期状态。
        CheckConstraint(
            (
                "status IN "
                "("
                "'pending', "
                "'processing', "
                "'ready', "
                "'failed', "
                "'archived'"
                ")"
            ),
            name=(
                "ck_knowledge_documents_"
                "status"
            ),
        ),

        # processing_stage 可以为空。
        # 不为空时必须是已知处理阶段。
        CheckConstraint(
            (
                "processing_stage IS NULL "
                "OR processing_stage IN "
                "("
                "'parsing', "
                "'cleaning', "
                "'chunking', "
                "'embedding', "
                "'indexing'"
                ")"
            ),
            name=(
                "ck_knowledge_documents_"
                "processing_stage"
            ),
        ),

        # 数据库记录的向量维度必须与 Vector(1024) 一致。
        CheckConstraint(
            (
                "embedding_dimensions = "
                f"{EMBEDDING_DIMENSIONS}"
            ),
            name=(
                "ck_knowledge_documents_"
                "embedding_dimensions"
            ),
        ),

        # 文档版本必须从 1 开始。
        CheckConstraint(
            "version > 0",
            name=(
                "ck_knowledge_documents_"
                "version_positive"
            ),
        ),
    )

    # 文档 UUID 字符串。
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    # 清洗后的文档标题。
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # 文档清洗前的原始正文。
    # 后续清洗算法升级时可以重新处理。
    raw_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # 清洗后用于切块和检索的正文。
    cleaned_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # manual、file 或 url。
    source_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,

        # 通过 SQLAlchemy 新建对象时使用。
        default=KnowledgeSourceType.MANUAL.value,

        # 直接执行 SQL 插入时由数据库使用。
        server_default=(
            KnowledgeSourceType.MANUAL.value
        ),
    )

    # 文件名、文件存储地址或网页 URL。
    # 手动输入文档时允许为 None。
    source_uri: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    # 清洗后正文的 SHA-256 指纹。
    #
    # 这里只创建普通索引，不设置全局唯一约束。
    # 因为未来不同企业可能合法保存相同内容。
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    # 文档整体生命周期状态。
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=(
            KnowledgeDocumentStatus
            .PENDING
            .value
        ),
        server_default=(
            KnowledgeDocumentStatus
            .PENDING
            .value
        ),
    )

    # internal：所有已登录用户可读；restricted：仅主管和管理员可读。
    visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="internal",
        server_default="internal",
    )

    # 当前正在执行的具体处理阶段。
    #
    # ready 或 pending 状态下通常为 None；
    # failed 状态可以保留失败时所在阶段。
    processing_stage: Mapped[
        str | None
    ] = mapped_column(
        String(20),
        nullable=True,
    )

    # 保存最近一次处理失败的错误说明。
    # 正常文档通常为 None。
    last_error: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    # 生成当前知识向量所使用的模型名称。
    embedding_model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # 保存当前模型输出的向量维度。
    embedding_dimensions: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
        default=EMBEDDING_DIMENSIONS,
        server_default=str(
            EMBEDDING_DIMENSIONS
        ),
    )

    # 生成 cleaned_content 的清洗器版本。
    cleaner_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # 生成知识片段的切块器版本。
    chunker_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # 当前文档版本。
    #
    # 第一阶段直接保存在文档表；
    # 第二阶段会重构成独立 DocumentVersion 表。
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    # 记录是谁创建了这篇知识文档。
    #
    # RESTRICT 表示文档仍存在时，
    # 数据库不允许直接删除对应用户。
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    # 记录是谁最后更新了当前文档版本。
    #
    # None 表示文档创建后还没有被修改过。
    # 更新到 version=2 以后，这里保存提交第 2 版的用户 ID。
    updated_by: Mapped[
        str | None
    ] = mapped_column(
        String(36),

        # 必须引用一名真实存在的用户。
        ForeignKey(
            "users.id",

            # 明确指定数据库外键约束名称。
            name=(
                "fk_knowledge_documents_"
                "updated_by_users"
            ),

            ondelete="RESTRICT",
        ),

        # 旧数据和从未修改过的文档允许为空。
        nullable=True,

        # 后续可以按照修改人筛选和审计文档。
        index=True,
    )

    # 文档记录创建时间。
    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        server_default=func.now(),
    )

    # 文档记录最后更新时间。
    updated_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # 建立一篇文档到多个片段的一对多关系。
    chunks: Mapped[
        list[KnowledgeChunkRecord]
    ] = relationship(
        # Chunk 类中对应的属性名称。
        back_populates="document",

        # 删除 Document ORM 对象时，
        # 同时删除其 Chunk ORM 对象。
        cascade="all, delete-orphan",

        # 如果数据库配置了 ON DELETE CASCADE，
        # 允许数据库直接负责删除未加载的片段。
        passive_deletes=True,

        # 从 document.chunks 读取时，
        # 默认按照 chunk_index 排列。
        order_by=(
            "KnowledgeChunkRecord."
            "chunk_index"
        ),
    )


# 表示数据库中的一个知识片段。
class KnowledgeChunkRecord(Base):
    """数据库 knowledge_chunks 表中的知识片段。"""

    # 指定数据库表名。
    __tablename__ = "knowledge_chunks"

    # 定义知识片段表的组合约束。
    __table_args__ = (
        # 同一篇文档中不能出现两个相同的 chunk_index。
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name=(
                "uq_knowledge_chunks_"
                "document_chunk_index"
            ),
        ),

        # chunk_index 必须从 0 开始。
        CheckConstraint(
            "chunk_index >= 0",
            name=(
                "ck_knowledge_chunks_"
                "chunk_index_non_negative"
            ),
        ),

        # 片段开始位置不能小于 0。
        CheckConstraint(
            "start_offset >= 0",
            name=(
                "ck_knowledge_chunks_"
                "start_offset_non_negative"
            ),
        ),

        # 结束位置必须位于开始位置之后。
        CheckConstraint(
            "end_offset > start_offset",
            name=(
                "ck_knowledge_chunks_"
                "end_after_start"
            ),
        ),
    )

    # 知识片段 UUID 字符串。
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    # 当前片段所属文档的 ID。
    #
    # 删除文档时，数据库自动删除对应片段。
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "knowledge_documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 片段在文档中的顺序，从 0 开始。
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 知识片段实际文本。
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # 知识片段的 SHA-256 内容指纹。
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    # 片段在 cleaned_content 中的开始位置。
    start_offset: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 片段在 cleaned_content 中的结束位置。
    end_offset: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 这个片段来自 PDF 的哪些页；普通文本或旧片段用空列表。
    page_numbers: Mapped[list[int]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )

    # 片段对应的 1024 维知识向量。
    #
    # PostgreSQL 中是 Vector(1024)；
    # SQLite 测试中是 JSON 浮点数列表。
    embedding: Mapped[
        list[float]
    ] = mapped_column(
        _EMBEDDING_COLUMN_TYPE,
        nullable=False,
    )

    # 片段记录的创建时间。
    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        server_default=func.now(),
    )

    # 建立片段到所属文档的多对一关系。
    document: Mapped[
        KnowledgeDocumentRecord
    ] = relationship(
        back_populates="chunks",
    )

# 表示知识文档的一个历史版本快照。
class KnowledgeDocumentVersionRecord(Base):
    """
    数据库 knowledge_document_versions 表中的历史版本。

    业务流程：
    当前文档准备更新
    → 把旧内容复制成历史版本
    → 保存是谁创建、谁替换了这个版本
    → 当前文档再更新成下一版本

    注意：
    本表只保存历史内容和处理配置，
    不保存旧向量和旧知识片段。
    恢复历史版本时会重新清洗、切块和生成向量。
    """

    # 指定数据库表名。
    __tablename__ = (
        "knowledge_document_versions"
    )

    # 定义历史版本表的数据库约束。
    __table_args__ = (
        # 同一篇文档不能出现两个相同的版本号。
        UniqueConstraint(
            "document_id",
            "version",
            name=(
                "uq_knowledge_document_versions_"
                "document_version"
            ),
        ),

        # 版本号必须从 1 开始。
        CheckConstraint(
            "version > 0",
            name=(
                "ck_knowledge_document_versions_"
                "version_positive"
            ),
        ),

        # 历史版本的来源类型也必须合法。
        CheckConstraint(
            (
                "source_type IN "
                "('manual', 'file', 'url')"
            ),
            name=(
                "ck_knowledge_document_versions_"
                "source_type"
            ),
        ),

        # 历史记录中的向量维度必须与系统配置一致。
        CheckConstraint(
            (
                "embedding_dimensions = "
                f"{EMBEDDING_DIMENSIONS}"
            ),
            name=(
                "ck_knowledge_document_versions_"
                "embedding_dimensions"
            ),
        ),
    )

    # 历史版本记录自身的唯一 ID。
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    # 这个历史版本属于哪一篇当前文档。
    #
    # 删除整篇当前文档时，
    # 数据库会同时删除它的全部历史版本。
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "knowledge_documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 当前快照对应的版本号。
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 这个历史版本当时使用的文档标题。
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # 这个历史版本当时保存的原始正文。
    raw_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # 这个历史版本当时保存的清洗后正文。
    cleaned_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # 这个历史版本的来源类型。
    source_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # 当时的文件地址、文件名或者网页 URL。
    source_uri: Mapped[
        str | None
    ] = mapped_column(
        String(2048),
        nullable=True,
    )

    # 当时清洗后正文的 SHA-256 指纹。
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    # 当时生成向量所使用的模型名称。
    embedding_model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # 当时向量模型输出的维度。
    embedding_dimensions: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
    )

    # 当时使用的文档清洗器版本。
    cleaner_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # 当时使用的文档切块器版本。
    chunker_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # 创建这个内容版本的用户。
    #
    # 对于 version=1，通常就是最初创建文档的人。
    # 对于后续版本，就是提交该次更新的人。
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    # 这个内容版本最初创建的时间。
    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        server_default=func.now(),
    )

    # 是哪个用户用新版本替换了这个历史版本。
    superseded_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    # 这个版本被新版本替换的时间。
    superseded_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        server_default=func.now(),
    )

# 保存一次入库任务，以及失败后重试所需的原始输入。
class KnowledgeIngestionJobRecord(Base):
    __tablename__ = "knowledge_ingestion_jobs"

    # 数据库也要拒绝非法值，不能只依赖 Python 或 API 校验。
    __table_args__ = (
        CheckConstraint(
            (
                "status IN "
                "('queued', 'running', 'cancelling', "
                "'succeeded', 'failed', 'cancelled')"
            ),
            name="ck_knowledge_ingestion_jobs_status",
        ),
        CheckConstraint(
            "source_type IN ('manual', 'file', 'url')",
            name="ck_knowledge_ingestion_jobs_source_type",
        ),
        CheckConstraint(
            "visibility IN ('internal', 'restricted')",
            name="ck_knowledge_ingestion_jobs_visibility",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_knowledge_ingestion_jobs_attempt_count",
        ),
        # 阶段允许为空，或属于已定义的处理阶段。
        CheckConstraint(
            "processing_stage IS NULL "
            "OR processing_stage IN "
            "('parsing', 'cleaning', 'chunking', "
            "'embedding', 'indexing')",
            name="ck_knowledge_ingestion_jobs_processing_stage",
        ),
        # 任务输入必须二选一：
        # 1. 已有正文，且没有待解析的文件信息；
        # 2. 没有正文，但有完整的文件信息，来源必须是 file。
        CheckConstraint(
            "("
            "raw_content IS NOT NULL "
            "AND storage_key IS NULL "
            "AND original_filename IS NULL"
            ") OR ("
            "raw_content IS NULL "
            "AND storage_key IS NOT NULL "
            "AND original_filename IS NOT NULL "
            "AND source_type = 'file'"
            ")",
            name="ck_knowledge_ingestion_jobs_input",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # 原始输入；本阶段先处理手动文本。
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # 文本任务：直接保存用户提交的正文。
    # 文件任务：解析前还没有正文，因此允许为空。
    raw_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    # 原始文件在存储目录中的内部标识。
    # 文件任务填写 save() 返回的值；文本任务为空。
    # 32 位 UUID 十六进制字符 + ".bin"，共 36 个字符。
    storage_key: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )

    # 用户上传时的原始文件名，例如“维修合同.pdf”。
    # Worker 用它选择解析器，并记录文档来源。
    # 它只是元数据，不能直接用于拼接磁盘读取路径。
    original_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048))
    visibility: Mapped[str] = mapped_column(String(20), nullable=False)

    # 谁提交任务；成功后关联生成的文档。
    requested_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 处理状态和失败信息。
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=KnowledgeIngestionJobStatus.QUEUED.value,
        server_default=KnowledgeIngestionJobStatus.QUEUED.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    # Worker 当前执行到的阶段，供页面查询。
    # 与任务整体状态 status 分开：
    # status=running，processing_stage=embedding。
    processing_stage: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # 本次领取任务的随机标识；尚未领取时为空。
    lease_token: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )

    # 本次领取的到期时间；尚未领取时为空。
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )