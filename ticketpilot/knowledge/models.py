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
from ticketpilot.knowledge.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入知识文档的来源和状态枚举。
from ticketpilot.knowledge.documents import (
    KnowledgeDocumentStatus,
    KnowledgeProcessingStage,
    KnowledgeSourceType,
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