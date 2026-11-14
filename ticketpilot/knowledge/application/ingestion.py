"""
实现知识文档的完整入库服务。

正常业务流程：
RawDocument
→ prepare_document() 清洗和切块
→ EmbeddingProvider 生成全部向量
→ 创建 KnowledgeDocumentRecord
→ 创建多个 KnowledgeChunkRecord
→ Repository 写入数据库
→ Session commit 提交事务
→ 返回已经就绪的知识文档

异常业务流程：
任意步骤发生异常
→ Session rollback 回滚事务
→ 不保存不完整的文档和片段
→ 抛出统一的知识入库异常

各层职责：
- Pipeline：负责清洗和切块。
- EmbeddingProvider：负责把文本转换成向量。
- Repository：负责执行数据库写入。
- Ingestion Service：负责组织整个流程和控制事务。
"""

# uuid4 用来给文档和每个知识片段生成唯一 ID。
from uuid import uuid4

# SQLAlchemyError 是数据库操作异常的公共父类。
from sqlalchemy.exc import SQLAlchemyError

# Session 代表当前数据库事务。
from sqlalchemy.orm import Session

# 导入切块器版本、默认参数和切块异常。
from ticketpilot.knowledge.domain.chunking import (
    CHUNKER_VERSION,
    DEFAULT_MAX_CHARACTERS,
    DEFAULT_OVERLAP_CHARACTERS,
    DocumentChunkingError,
)

# 导入文档清洗过程中可能出现的异常。
from ticketpilot.knowledge.domain.cleaning import (
    DocumentCleaningError,
)

# 导入数据库规定的固定向量维度。
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入原始文档和文档状态。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
    RawDocument,
    KnowledgeProcessingStage,
)

# 导入向量模型的统一接口和异常。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
    EmbeddingProviderError,
    InvalidEmbeddingInputError,
    InvalidEmbeddingResponseError,
)

# 导入文档和知识片段的 ORM 模型。
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)

# 导入“清洗加切块”的预处理流水线。
from ticketpilot.knowledge.domain.pipeline import (
    prepare_document,
)

# 导入知识库数据库保存函数和异常。
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeRepositoryError,
    add_document,
)

from typing import Literal

from collections.abc import Callable

# 表示完整知识入库流程执行失败。
class KnowledgeIngestionError(RuntimeError):
    """知识文档无法完整写入知识库。"""


# 这个函数负责把一篇原始文档完整处理并保存到知识库。
def ingest_document(
    *,
    # 当前业务流程使用的数据库 Session。
    session: Session,

    # 尚未经过清洗和切块的原始文档。
    raw_document: RawDocument,

    # 创建这篇文档的用户 ID。
    created_by: str,

    # 负责把文本转换成向量的模型接口。
    embedding_provider: EmbeddingProvider,

    # 创建时就确定可见性，旧调用默认保持 internal。
    visibility: Literal["internal", "restricted"] = "internal",

    # 每个知识片段允许的最大字符数。
    max_characters: int = (
        DEFAULT_MAX_CHARACTERS
    ),

    # 相邻知识片段重复保留的字符数。
    overlap_characters: int = (
        DEFAULT_OVERLAP_CHARACTERS
    ),
    # 后台任务重试时使用固定 ID；普通接口不传，仍自动生成。
    document_id: str | None = None,

    # 同步调用可以自行提交；后台 Worker 传 False，最后统一提交。
    commit: bool = True,

    # Worker 提供的阶段通知函数；旧调用可以不传。
    on_stage: Callable[[KnowledgeProcessingStage], None] | None = None,

) -> KnowledgeDocumentRecord:
    """完成文档预处理、向量生成和数据库保存。"""

    # try 内是完整的知识入库流程。
    try:
        # 第一步：清洗原始文档并切分成知识片段。
        prepared_document = prepare_document(
            raw_document,
            max_characters=max_characters,
            overlap_characters=overlap_characters,

            # 把同一个通知函数继续交给清洗、分块流水线。
            on_stage=on_stage,
        )

        # 提取所有片段的文字，准备批量生成向量。
        chunk_texts = [
            chunk.content
            for chunk in prepared_document.chunks
        ]

        # 真正调用模型之前，先报告生成向量阶段。
        if on_stage is not None:
            on_stage(KnowledgeProcessingStage.EMBEDDING)

        # 第二步：一次性为全部知识片段生成向量。
        embedding_batch = (
            embedding_provider.embed_texts(
                chunk_texts
            )
        )

        # 每个知识片段都必须正好对应一个向量。
        if (
            embedding_batch.count
            != prepared_document.chunk_count
        ):
            raise InvalidEmbeddingResponseError(
                "向量数量与知识片段数量不一致"
            )

        # 模型声明的维度必须符合数据库 Vector(1024)。
        if (
            embedding_batch.dimensions
            != EMBEDDING_DIMENSIONS
        ):
            raise InvalidEmbeddingResponseError(
                "向量维度与数据库结构不一致"
            )

        # 每个实际向量的长度也必须是 1024。
        if any(
            len(vector)
            != embedding_batch.dimensions
            for vector in embedding_batch.vectors
        ):
            raise InvalidEmbeddingResponseError(
                "实际向量维度与声明维度不一致"
            )

        # 取得经过清洗的完整文档。
        cleaned_document = (
            prepared_document.cleaned_document
        )

        # 这是开始写入文档之前的最后一次阶段通知。
        # 后面保存文档、片段和最终成功状态时，不再单独提交进度。
        if on_stage is not None:
            on_stage(KnowledgeProcessingStage.INDEXING)

        # 第三步：创建整篇文档对应的 ORM 对象。
        document_record = KnowledgeDocumentRecord(
            id=(
                document_id
                if document_id is not None
                else str(uuid4())
            ),
            title=cleaned_document.title,
            raw_content=(
                cleaned_document.raw_content
            ),
            cleaned_content=(
                cleaned_document.cleaned_content
            ),
            source_type=(
                cleaned_document.source_type.value
            ),
            source_uri=(
                cleaned_document.source_uri
            ),
            content_hash=(
                cleaned_document.content_hash
            ),
            status=(
                KnowledgeDocumentStatus.READY.value
            ),

            # 与文档和片段一起保存，避免先公开再改权限。
            visibility=visibility,

            processing_stage=None,
            last_error=None,
            embedding_model=(
                embedding_batch.model_name
            ),
            embedding_dimensions=(
                embedding_batch.dimensions
            ),
            cleaner_version=(
                cleaned_document.cleaner_version
            ),
            chunker_version=CHUNKER_VERSION,
            version=1,
            created_by=created_by,
        )

        # 第四步：把片段和向量一一配对。
        for chunk, vector in zip(
            prepared_document.chunks,
            embedding_batch.vectors,
            strict=True,
        ):
            # 为当前片段创建 ORM 对象，并放进文档的 chunks。
            document_record.chunks.append(
                KnowledgeChunkRecord(
                    id=str(uuid4()),
                    chunk_index=(
                        chunk.chunk_index
                    ),
                    content=chunk.content,
                    content_hash=(
                        chunk.content_hash
                    ),
                    start_offset=(
                        chunk.start_offset
                    ),
                    end_offset=(
                        chunk.end_offset
                    ),
                    # 把分块计算出的来源页码保存到数据库。
                    # TextChunk 使用 tuple；JSON 字段保存 list。
                    page_numbers=list(
                        chunk.page_numbers
                    ),
                    # EmbeddingBatch 使用 tuple，
                    # ORM 的 JSON/Vector 字段使用 list。
                    embedding=list(
                        vector
                    ),
                )
            )

        # 第五步：让 Repository 执行 add() 和 flush()。
        saved_document = add_document(
            session,
            document_record,
        )

        # 第六步：全部步骤成功后统一提交事务。
        if commit:
            session.commit()

        # 返回已经保存的文档 ORM 对象。
        return saved_document

    # 捕获可以预期的清洗、切块、向量和数据库异常。
    except (
        DocumentCleaningError,
        DocumentChunkingError,
        InvalidEmbeddingInputError,
        EmbeddingProviderError,
        KnowledgeRepositoryError,
        SQLAlchemyError,
    ) as error:
        # 任意步骤失败，都撤销当前事务中的数据库修改。
        session.rollback()

        # 向上层只暴露统一的知识入库异常，
        # from error 会保留真正的底层原因。
        raise KnowledgeIngestionError(
            "知识文档入库失败"
        ) from error