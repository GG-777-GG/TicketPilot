"""
实现知识库模块的数据库访问层。

文件职责：
- 接收已经构造完成的知识文档 ORM 对象。
- 将文档及其全部知识片段加入 Session。
- 使用 flush() 提前执行 SQL 并发现数据库错误。
- 不在 Repository 内部提交事务。
- 把 SQLAlchemy 异常转换成知识库业务异常。

正常流程：
Ingestion Service
→ 创建 KnowledgeDocumentRecord
→ 把 KnowledgeChunkRecord 放入 document.chunks
→ add_document()
→ session.add(document)
→ ORM 级联加入全部 chunks
→ session.flush()
→ 返回 Ingestion Service
→ Service 执行 session.commit()

异常流程：
session.flush()
→ SQLAlchemyError
→ KnowledgeRepositoryError
→ 返回 Ingestion Service
→ Service 执行 session.rollback()

为什么 Repository 不执行 commit：
一次知识入库以后可能同时包含：
1. 保存文档。
2. 保存全部知识片段。
3. 更新入库任务。
4. 保存审计记录。

这些操作应该全部成功或者全部撤销，因此事务最终由
Ingestion Service 统一 commit 或 rollback。
"""

# SQLAlchemyError 是 SQLAlchemy 数据库异常的公共父类。
from sqlalchemy.exc import SQLAlchemyError

# Session 表示一次数据库操作会话和事务边界。
from sqlalchemy.orm import Session

# 只允许 ready 状态的文档参与检索。
from ticketpilot.knowledge.documents import (
    KnowledgeDocumentStatus,
)

# 导入知识文档 ORM 模型。
from ticketpilot.knowledge.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)

# Sequence 表示有顺序、可以遍历的向量输入。
from collections.abc import Sequence

# dataclass 用来保存数据库查询返回的一行结果。
from dataclasses import dataclass

# select 用来构造 SQLAlchemy 查询。
from sqlalchemy import select


# 表示知识库 Repository 执行数据库操作失败。
class KnowledgeRepositoryError(RuntimeError):
    """知识库数据库操作失败。"""


# 把一篇文档及其全部知识片段加入当前数据库事务。
def add_document(
    # 接收当前请求或者当前业务流程使用的 Session。
    session: Session,

    # 接收已经构造完成的文档 ORM 对象。
    document: KnowledgeDocumentRecord,
) -> KnowledgeDocumentRecord:
    """把知识文档及其全部片段加入当前事务。"""

    # 捕获 SQLAlchemy 在写入数据库时产生的异常。
    try:
        # 把文档对象加入当前 Session。
        session.add(
            document
        )

        # KnowledgeDocumentRecord.chunks 配置了级联关系。
        # 因此加入 document 时，它包含的全部 chunk 也会被加入。
        #
        # flush() 会立即向数据库发送 INSERT，
        # 让外键、唯一约束和字段约束尽早接受检查。
        #
        # flush() 不等于 commit：
        # 此时数据仍在当前事务中，之后依然可以 rollback。
        session.flush()

    # 捕获连接失败、约束冲突等数据库异常。
    except SQLAlchemyError as error:
        # Repository 不在这里 rollback。
        # 因为事务的最终控制权属于 Ingestion Service。
        #
        # 使用 from error 保留真正的数据库异常链，
        # 方便后台日志排查具体原因。
        raise KnowledgeRepositoryError(
            "保存知识文档失败"
        ) from error

    # flush 成功后返回仍由当前 Session 管理的文档对象。
    return document

# 这个类负责保存 Repository 查询到的一条知识片段记录。
@dataclass(
    frozen=True,
    slots=True,
)
class KnowledgeChunkSearchRow:
    # 知识片段 ID。
    chunk_id: str

    # 片段所属文档 ID。
    document_id: str

    # 所属文档标题。
    document_title: str

    # 当前文档版本。
    document_version: int

    # 数据库中保存的来源类型字符串。
    source_type: str

    # 文件名或网页地址。
    source_uri: str | None

    # 片段在文档中的顺序。
    chunk_index: int

    # 片段正文。
    content: str

    # 片段在清洗后正文中的位置。
    start_offset: int
    end_offset: int

    # PostgreSQL 计算出的余弦距离。
    cosine_distance: float

# 这个函数负责使用 pgvector 查找最相似的知识片段。
def search_ready_chunks_by_cosine(
    session: Session,
    *,
    # 用户问题生成的查询向量。
    query_embedding: Sequence[float],

    # 只搜索由同一个模型生成的文档向量。
    embedding_model: str,

    # 只搜索维度相同的文档向量。
    embedding_dimensions: int,

    # 最多返回多少个片段。
    top_k: int,

    # 最低允许的余弦相似度。
    min_similarity: float,
) -> tuple[
    KnowledgeChunkSearchRow,
    ...,
]:
    """使用余弦距离查询 ready 状态的知识片段。"""

    # pgvector 需要普通 list 作为查询向量。
    query_vector = list(
        query_embedding
    )

    # cosine_distance() 会生成 pgvector 的 <=> 运算。
    #
    # 例如：
    # knowledge_chunks.embedding <=> '[...]'
    distance_expression = (
        KnowledgeChunkRecord
        .embedding
        .cosine_distance(
            query_vector
        )
    )

    # 只查询返回结果真正需要的字段。
    statement = (
        select(
            KnowledgeChunkRecord.id.label(
                "chunk_id"
            ),
            KnowledgeChunkRecord.document_id.label(
                "document_id"
            ),
            KnowledgeDocumentRecord.title.label(
                "document_title"
            ),
            KnowledgeDocumentRecord.version.label(
                "document_version"
            ),
            KnowledgeDocumentRecord.source_type.label(
                "source_type"
            ),
            KnowledgeDocumentRecord.source_uri.label(
                "source_uri"
            ),
            KnowledgeChunkRecord.chunk_index.label(
                "chunk_index"
            ),
            KnowledgeChunkRecord.content.label(
                "content"
            ),
            KnowledgeChunkRecord.start_offset.label(
                "start_offset"
            ),
            KnowledgeChunkRecord.end_offset.label(
                "end_offset"
            ),

            # 把数据库计算结果命名为 cosine_distance。
            distance_expression.label(
                "cosine_distance"
            ),
        )

        # 每个片段必须连接到所属文档。
        .join(
            KnowledgeDocumentRecord,
            (
                KnowledgeChunkRecord.document_id
                == KnowledgeDocumentRecord.id
            ),
        )

        # 过滤不能参与当前查询的数据。
        .where(
            # 只有处理完成的文档才能参与检索。
            KnowledgeDocumentRecord.status
            == KnowledgeDocumentStatus.READY.value,

            # 不同模型的向量不能混合比较。
            KnowledgeDocumentRecord.embedding_model
            == embedding_model,

            # 向量维度必须保持一致。
            KnowledgeDocumentRecord.embedding_dimensions
            == embedding_dimensions,

            # similarity = 1 - distance
            # 所以 distance 必须小于等于 1 - 最低相似度。
            distance_expression
            <= (
                1.0
                - min_similarity
            ),
        )

        # 距离越小越相似，因此使用升序排列。
        .order_by(
            distance_expression.asc(),

            # 相同距离时使用 ID 保证顺序稳定。
            KnowledgeChunkRecord.id.asc(),
        )

        # 只返回最前面的 top_k 项。
        .limit(
            top_k
        )
    )

    try:
        # 真正把 SELECT 发送给数据库。
        rows = session.execute(
            statement
        ).all()

    # 捕获连接失败或 SQL 执行错误。
    except SQLAlchemyError as error:
        raise KnowledgeRepositoryError(
            "搜索知识片段失败"
        ) from error

    # 把 SQLAlchemy Row 转换成明确的数据对象。
    return tuple(
        KnowledgeChunkSearchRow(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            document_title=(
                row.document_title
            ),
            document_version=(
                row.document_version
            ),
            source_type=row.source_type,
            source_uri=row.source_uri,
            chunk_index=row.chunk_index,
            content=row.content,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
            cosine_distance=float(
                row.cosine_distance
            ),
        )
        for row in rows
    )