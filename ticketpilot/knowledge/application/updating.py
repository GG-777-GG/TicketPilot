"""
实现知识文档版本更新服务。

完整业务流程：
接收新的标题、正文和 expected_version
→ 查询当前知识文档
→ 检查文档状态
→ 检查 expected_version
→ 清洗新正文
→ 检查内容是否真的发生变化
→ 重新切块
→ 重新生成向量
→ 保存当前版本的历史快照
→ 删除旧知识片段
→ 保存新知识片段
→ 当前文档 version + 1
→ 提交整个数据库事务

异常流程：
任意步骤失败
→ Session rollback
→ 当前文档、历史版本和知识片段都保持原状
"""


# uuid4 用来生成历史版本和新知识片段的唯一 ID。
from uuid import uuid4

# SQLAlchemyError 是数据库异常的公共父类。
from sqlalchemy.exc import SQLAlchemyError

# Session 表示当前更新业务使用的数据库事务。
from sqlalchemy.orm import Session

# 导入切块器版本、默认配置和切块异常。
from ticketpilot.knowledge.domain.chunking import (
    CHUNKER_VERSION,
    DEFAULT_MAX_CHARACTERS,
    DEFAULT_OVERLAP_CHARACTERS,
    DocumentChunkingError,
)

# 导入文档清洗异常。
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
)

# 导入向量 Provider 接口和相关异常。
from ticketpilot.knowledge.embeddings.base import (
    # 保存一次批量向量结果。
    EmbeddingBatch,
    EmbeddingProvider,
    EmbeddingProviderError,
    InvalidEmbeddingInputError,
    InvalidEmbeddingResponseError,
)

# 导入文档、历史版本和片段 ORM 模型。
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentVersionRecord,
)

# 导入文档清洗和切块流水线。
from ticketpilot.knowledge.domain.pipeline import (
    # 表示已经完成清洗和切块的文档。
    PreparedDocument,

    # 执行清洗和切块。
    prepare_document,
)

# 导入更新流程需要的 Repository 函数。
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeRepositoryError,
    add_document_version,
    # 第一次检查使用普通查询，不锁定数据库行。
    get_document_record_by_id,
    # 最终写入前使用行锁查询。
    get_document_record_for_update,
    replace_document_chunks,
)


# 表示完整的知识文档更新流程执行失败。
class KnowledgeDocumentUpdateError(RuntimeError):
    """知识文档更新过程中发生内部错误。"""


# 表示客户端提交的版本号已经过期。
class KnowledgeDocumentVersionConflictError(
    ValueError
):
    """客户端版本号与数据库当前版本不一致。"""


# 表示当前文档状态不允许执行内容更新。
class InvalidKnowledgeDocumentUpdateStateError(
    ValueError
):
    """知识文档当前状态不允许更新内容。"""


# 表示客户端提交的内容与当前版本没有区别。
class KnowledgeDocumentNoChangesError(
    ValueError
):
    """知识文档标题和正文没有发生变化。"""

# 这个内部函数负责验证向量模型返回的数据。
def _validate_embedding_batch(
    # Ollama 返回的一批向量。
    embedding_batch: EmbeddingBatch,

    # 本次需要生成向量的知识片段数量。
    expected_count: int,
) -> None:
    """
    验证向量数量和维度。

    函数名前面的下划线表示：
    这是 updating.py 内部使用的函数，
    Router 和其他模块不应该直接调用。
    """

    # 每个知识片段必须正好对应一个向量。
    if (
        embedding_batch.count
        != expected_count
    ):
        raise InvalidEmbeddingResponseError(
            "向量数量与知识片段数量不一致"
        )

    # 模型声明的向量维度必须符合数据库结构。
    if (
        embedding_batch.dimensions
        != EMBEDDING_DIMENSIONS
    ):
        raise InvalidEmbeddingResponseError(
            "向量维度与数据库结构不一致"
        )

    # 每个实际向量的长度也必须符合模型声明。
    if any(
        len(vector)
        != embedding_batch.dimensions
        for vector in embedding_batch.vectors
    ):
        raise InvalidEmbeddingResponseError(
            "实际向量维度与声明维度不一致"
        )


# 这个内部函数负责把当前文档复制成历史版本快照。
def _build_document_version_snapshot(
    # 更新之前的当前文档 ORM 对象。
    document: KnowledgeDocumentRecord,

    # 提交新版本、让旧版本成为历史的用户 ID。
    superseded_by: str,
) -> KnowledgeDocumentVersionRecord:
    """
    把当前文档内容复制成不可变的历史版本。

    这里只构造 ORM 对象，
    不执行 session.add、flush 或 commit。
    """

    # version=1 时还没有 updated_by，
    # 当前版本的创建者就是最初的 created_by。
    #
    # version>=2 时，updated_by 保存当前版本的创建者。
    version_created_by = (
        document.updated_by
        or document.created_by
    )

    # 第一版的创建时间使用文档创建时间。
    #
    # 后续版本使用当前文档最后更新时间。
    version_created_at = (
        document.created_at
        if document.version == 1
        else document.updated_at
    )

    # 创建一条历史版本 ORM 对象。
    return KnowledgeDocumentVersionRecord(
        # 历史记录拥有独立的唯一 ID。
        id=str(
            uuid4()
        ),

        # 历史版本属于当前这篇文档。
        document_id=document.id,

        # 保存更新之前的版本号。
        version=document.version,

        # 复制更新之前的标题。
        title=document.title,

        # 复制更新之前的原始正文。
        raw_content=document.raw_content,

        # 复制更新之前的清洗后正文。
        cleaned_content=(
            document.cleaned_content
        ),

        # 复制文档来源信息。
        source_type=document.source_type,
        source_uri=document.source_uri,

        # 复制旧版本的内容指纹。
        content_hash=document.content_hash,

        # 记录旧版本使用的向量模型。
        embedding_model=(
            document.embedding_model
        ),

        # 记录旧版本的向量维度。
        embedding_dimensions=(
            document.embedding_dimensions
        ),

        # 记录旧版本使用的清洗器。
        cleaner_version=(
            document.cleaner_version
        ),

        # 记录旧版本使用的切块器。
        chunker_version=(
            document.chunker_version
        ),

        # 保存这个旧版本最初由谁创建。
        created_by=version_created_by,

        # 保存这个旧版本最初创建的时间。
        created_at=version_created_at,

        # 保存是谁提交了下一版。
        superseded_by=superseded_by,
    )


# 这个内部函数负责组合新片段和对应向量。
def _build_new_chunk_records(
    # 已经完成清洗和切块的文档。
    prepared_document: PreparedDocument,

    # Ollama 为全部片段生成的向量。
    embedding_batch: EmbeddingBatch,
) -> list[KnowledgeChunkRecord]:
    """
    为每个文本片段创建一个知识片段 ORM 对象。

    文本片段和向量按照相同位置一一配对：
    chunks[0] 对应 vectors[0]
    chunks[1] 对应 vectors[1]
    """

    # 保存即将返回的新 ORM 片段。
    new_chunks: list[
        KnowledgeChunkRecord
    ] = []

    # strict=True 保证两个序列长度必须完全相同。
    for chunk, vector in zip(
        prepared_document.chunks,
        embedding_batch.vectors,
        strict=True,
    ):
        # 为当前文本片段创建 ORM 对象。
        new_chunks.append(
            KnowledgeChunkRecord(
                # 每个新片段都使用新的唯一 ID。
                id=str(
                    uuid4()
                ),

                # 保存片段在文档中的顺序。
                chunk_index=(
                    chunk.chunk_index
                ),

                # 保存片段正文。
                content=chunk.content,

                # 保存片段正文的 SHA-256 指纹。
                content_hash=(
                    chunk.content_hash
                ),

                # 保存片段在清洗后正文中的起始位置。
                start_offset=(
                    chunk.start_offset
                ),

                # 保存片段在清洗后正文中的结束位置。
                end_offset=(
                    chunk.end_offset
                ),
                # 更新文档时重新分块，也要保存新片段的来源页码。
                # tuple 转成列表，供数据库 JSON 字段保存。
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

    # 返回全部新知识片段。
    return new_chunks

# 这个内部函数负责检查当前文档是否允许更新。
def _validate_document_update(
    # 数据库中的当前文档。
    document: KnowledgeDocumentRecord,

    # 客户端读取文档时看到的版本号。
    expected_version: int,

    # 新版本清洗后的标题。
    new_title: str,

    # 新版本清洗后正文的指纹。
    new_content_hash: str,
) -> None:
    """检查状态、版本号以及内容是否真的发生变化。"""

    # 只有 ready 文档可以更新内容。
    #
    # archived 文档必须先恢复；
    # pending、processing、failed 文档需要其他处理流程。
    if document.status != (
        KnowledgeDocumentStatus.READY.value
    ):
        raise InvalidKnowledgeDocumentUpdateStateError(
            "只有 ready 状态的知识文档可以更新"
        )

    # 客户端版本必须与数据库当前版本完全一致。
    if document.version != expected_version:
        raise KnowledgeDocumentVersionConflictError(
            (
                "文档版本已经发生变化，"
                f"当前版本为 {document.version}"
            )
        )

    # 标题和正文指纹都相同，说明没有真实变化。
    if (
        document.title == new_title
        and document.content_hash
        == new_content_hash
    ):
        raise KnowledgeDocumentNoChangesError(
            "知识文档标题和正文没有发生变化"
        )

# 这个函数负责更新知识文档并生成历史版本。
def update_document(
    *,
    # 当前请求使用的数据库 Session。
    session: Session,

    # 要更新的知识文档 ID。
    document_id: str,

    # 客户端提交的新标题和新正文。
    raw_document: RawDocument,

    # 客户端读取文档时看到的版本号。
    expected_version: int,

    # 执行本次更新的用户 ID。
    updated_by: str,

    # 负责生成新知识片段向量的 Provider。
    embedding_provider: EmbeddingProvider,

    # 每个知识片段允许的最大字符数。
    max_characters: int = (
        DEFAULT_MAX_CHARACTERS
    ),

    # 相邻片段之间保留的重叠字符数。
    overlap_characters: int = (
        DEFAULT_OVERLAP_CHARACTERS
    ),
) -> KnowledgeDocumentRecord | None:
    """
    更新知识文档并保存旧版本。

    返回更新后的 ORM 文档。
    找不到文档时返回 None。
    """

    try:
        # 第一步：清洗新标题和正文，并重新切块。
        #
        # 这一步只处理内存数据，还没有修改数据库。
        prepared_document = prepare_document(
            raw_document,
            max_characters=max_characters,
            overlap_characters=(
                overlap_characters
            ),
        )

        # 取得清洗后的完整新文档。
        cleaned_document = (
            prepared_document.cleaned_document
        )

        # 第二步：普通查询当前文档。
        #
        # 此时不使用 FOR UPDATE，
        # 避免后面调用 Ollama 时长时间锁住数据库。
        document = get_document_record_by_id(
            session,
            document_id,
        )

        # 文档不存在。
        if document is None:
            # 结束普通查询产生的只读事务。
            session.rollback()
            return None

        # 提前检查状态、版本和内容变化。
        _validate_document_update(
            document,
            expected_version,
            cleaned_document.title,
            cleaned_document.content_hash,
        )

        # 结束第一次普通查询产生的事务。
        #
        # 后面生成向量可能耗时较长，
        # 这期间不应该占用数据库事务。
        session.rollback()

        # 第三步：提取全部新知识片段的正文。
        chunk_texts = [
            chunk.content
            for chunk in prepared_document.chunks
        ]

        # 调用向量 Provider 批量生成新向量。
        embedding_batch = (
            embedding_provider.embed_texts(
                chunk_texts
            )
        )

        # 验证向量数量和维度。
        _validate_embedding_batch(
            embedding_batch,
            prepared_document.chunk_count,
        )

        # 把文本片段和向量组合成新的 ORM 片段。
        new_chunks = _build_new_chunk_records(
            prepared_document,
            embedding_batch,
        )

        # 第四步：最终写入前重新查询并锁定文档行。
        #
        # 如果其他管理员刚刚更新了文档，
        # 这里会读取到最新版本。
        document = get_document_record_for_update(
            session,
            document_id,
        )

        # 生成向量期间文档可能被其他请求删除。
        if document is None:
            session.rollback()
            return None

        # 再检查一次状态、版本和内容。
        #
        # 这是防止并发覆盖的关键检查。
        _validate_document_update(
            document,
            expected_version,
            cleaned_document.title,
            cleaned_document.content_hash,
        )

        # 第五步：把当前版本复制成历史快照。
        history = (
            _build_document_version_snapshot(
                document,
                superseded_by=updated_by,
            )
        )

        # 把历史版本加入当前事务并执行 flush。
        add_document_version(
            session,
            history,
        )

        # 第六步：把当前文档字段更新成新版本。
        document.title = (
            cleaned_document.title
        )
        document.raw_content = (
            cleaned_document.raw_content
        )
        document.cleaned_content = (
            cleaned_document.cleaned_content
        )
        document.content_hash = (
            cleaned_document.content_hash
        )

        # 更新后的文档可以参与 RAG 检索。
        document.status = (
            KnowledgeDocumentStatus.READY.value
        )

        # 更新成功后不处于任何处理中间阶段。
        document.processing_stage = None

        # 清除之前可能保存的处理错误。
        document.last_error = None

        # 记录本次实际使用的向量模型和维度。
        document.embedding_model = (
            embedding_batch.model_name
        )
        document.embedding_dimensions = (
            embedding_batch.dimensions
        )

        # 记录本次使用的清洗器和切块器版本。
        document.cleaner_version = (
            cleaned_document.cleaner_version
        )
        document.chunker_version = (
            CHUNKER_VERSION
        )

        # 当前文档版本号增加 1。
        document.version += 1

        # 记录是谁创建了这个新版本。
        document.updated_by = updated_by

        # 第七步：删除旧片段并保存全部新片段。
        replace_document_chunks(
            session,
            document,
            new_chunks,
        )

        # 刷新数据库生成或更新的时间字段。
        session.refresh(
            document
        )

        # 第八步：全部操作成功后统一提交事务。
        session.commit()

        # 返回已经更新的当前文档。
        return document

    # 这些是可预期的业务冲突，
    # Router 会把它们转换成 HTTP 409。
    except (
        KnowledgeDocumentVersionConflictError,
        InvalidKnowledgeDocumentUpdateStateError,
        KnowledgeDocumentNoChangesError,
    ):
        session.rollback()
        raise

    # 这些是清洗、切块、向量或数据库内部错误。
    except (
        DocumentCleaningError,
        DocumentChunkingError,
        InvalidEmbeddingInputError,
        EmbeddingProviderError,
        KnowledgeRepositoryError,
        SQLAlchemyError,
    ) as error:
        session.rollback()

        # Router 不应该知道底层具体失败原因。
        raise KnowledgeDocumentUpdateError(
            "知识文档更新失败"
        ) from error


# 这个函数负责只修改标题，不重新清洗正文或生成向量。
def update_document_title(
    *,
    session: Session,
    document_id: str,
    title: str,
    expected_version: int,
    updated_by: str,
) -> KnowledgeDocumentRecord | None:
    """保存旧版快照，再创建一个仅标题变化的新版本。"""

    try:
        # 锁定要修改的文档，防止两个请求同时覆盖对方。
        document = get_document_record_for_update(
            session,
            document_id,
        )

        if document is None:
            session.rollback()
            return None

        # 复用现有检查：必须为 ready、版本一致、标题确实变化。
        # 正文指纹传入旧值，表示本次不改变正文。
        _validate_document_update(
            document,
            expected_version,
            title,
            document.content_hash,
        )

        # 修改前先保存完整的旧版本快照。
        history = _build_document_version_snapshot(
            document,
            superseded_by=updated_by,
        )
        add_document_version(session, history)

        # 只修改标题、版本号和修改人。
        document.title = title
        document.version += 1
        document.updated_by = updated_by

        # 执行 SQL 并刷新数据库生成的更新时间。
        session.flush()
        session.refresh(document)

        # 历史快照和新标题在同一个事务中提交。
        session.commit()
        return document

    # 版本冲突、状态不对、标题未变化：保留明确的业务异常。
    except (
        KnowledgeDocumentVersionConflictError,
        InvalidKnowledgeDocumentUpdateStateError,
        KnowledgeDocumentNoChangesError,
    ):
        session.rollback()
        raise

    # 数据库异常：回滚并转换成统一的更新失败异常。
    except (
        KnowledgeRepositoryError,
        SQLAlchemyError,
    ) as error:
        session.rollback()
        raise KnowledgeDocumentUpdateError(
            "知识文档标题更新失败"
        ) from error