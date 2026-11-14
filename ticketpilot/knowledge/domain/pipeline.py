"""
编排第一阶段的知识文档预处理流水线。

文件职责：
- 接收一篇 RawDocument。
- 调用文档清洗器。
- 调用文本切块器。
- 返回清洗结果和全部知识片段。
- 避免 API 层直接编排多个底层步骤。

正常流程：
RawDocument
→ clean_document()
→ CleanedDocument
→ chunk_document()
→ tuple[TextChunk, ...]
→ PreparedDocument

异常流程：
- 清洗失败时，DocumentCleaningError 会继续向上传递。
- 切块失败时，DocumentChunkingError 会继续向上传递。
- 任意步骤失败时，不会返回部分处理结果。
- 当前流水线尚未写入数据库，所以不会产生残留数据。

第一阶段范围：
- 同步执行清洗和切块。
- 不调用 Embedding。
- 不写入 PostgreSQL。

后续重构：
PreparedDocument
→ 批量生成 Embedding
→ 数据库事务
→ 保存 Document 和 Chunk
→ 全部成功后 commit
→ 任意失败则 rollback

完整调用链：
API
→ prepare_document()
→ clean_document()
→ chunk_document()
→ PreparedDocument
→ IngestionService
→ Embedding
→ Repository
→ PostgreSQL
"""

# dataclass 用来保存不可变的文档预处理结果。
from dataclasses import dataclass

# 导入切块默认参数、片段对象和切块函数。
from ticketpilot.knowledge.domain.chunking import (
    DEFAULT_MAX_CHARACTERS,
    DEFAULT_OVERLAP_CHARACTERS,
    TextChunk,
    chunk_document,
)

# Callable 表示参数可以是一个函数。
from collections.abc import Callable

# 导入文档清洗函数。
from ticketpilot.knowledge.domain.cleaning import (
    clean_document,
)

# 导入清洗前和清洗后的文档对象。
from ticketpilot.knowledge.domain.documents import (
    CleanedDocument,
    RawDocument,
    KnowledgeProcessingStage,
)


# 表示一篇文档完成清洗和切块后的结果。
@dataclass(
    frozen=True,
    slots=True,
)
class PreparedDocument:
    """保存清洗后的文档和按顺序生成的知识片段。"""

    # 保存清洗完成的完整文档。
    cleaned_document: CleanedDocument

    # 使用 tuple 保存全部片段。
    # tuple 不允许调用 append() 修改处理结果。
    chunks: tuple[TextChunk, ...]

    # 返回本次生成的片段数量。
    @property
    def chunk_count(self) -> int:
        # tuple 的长度就是知识片段数量。
        return len(
            self.chunks
        )


# 执行第一阶段的完整文档预处理。
def prepare_document(
    document: RawDocument,
    *,
    max_characters: int = (
        DEFAULT_MAX_CHARACTERS
    ),
    overlap_characters: int = (
        DEFAULT_OVERLAP_CHARACTERS
    ),
    # 可选的阶段通知函数；不传时保持原来的同步行为。
    on_stage: Callable[[KnowledgeProcessingStage], None] | None = None,
) -> PreparedDocument:
    """依次清洗和切分一篇原始文档。"""

    # 只通知当前阶段，不在预处理流水线里操作数据库。
    if on_stage is not None:
        on_stage(KnowledgeProcessingStage.CLEANING)
    # 第一步：清洗原始标题和正文。
    cleaned_document = clean_document(
        document
    )

    # 第二步：把清洗后的正文拆成知识片段。
    # 清洗完成，即将开始切分知识片段。
    if on_stage is not None:
        on_stage(KnowledgeProcessingStage.CHUNKING)
    chunks = chunk_document(
        cleaned_document,
        max_characters=max_characters,
        overlap_characters=(
            overlap_characters
        ),
    )

    # 第三步：返回一个完整且不可变的预处理结果。
    return PreparedDocument(
        cleaned_document=cleaned_document,

        # chunk_document() 返回 list，
        # 这里转换成 tuple，防止后续意外修改。
        chunks=tuple(
            chunks
        ),
    )