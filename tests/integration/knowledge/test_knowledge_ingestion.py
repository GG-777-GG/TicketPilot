"""
使用真实临时数据库测试知识文档完整入库流程。

测试范围：
RawDocument
→ 清洗
→ 切块
→ 模拟生成向量
→ 创建 ORM 对象
→ Repository
→ SQLite 临时数据库
→ 重新查询文档和片段

说明：
- 数据库使用真实的内存 SQLite。
- 向量模型仍然使用模拟对象。
- SQLite 使用 JSON 代替 PostgreSQL Vector 字段。
"""

# MagicMock 用来模拟 Embedding Provider。
from unittest.mock import MagicMock

# Session 用于数据库 fixture 的类型注解。
from sqlalchemy.orm import Session

# 用户 ORM 类型用于 database_user fixture。
from ticketpilot.identity.models import (
    UserRecord,
)

# 导入固定向量维度。
from ticketpilot.knowledge.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入原始文档和文档状态。
from ticketpilot.knowledge.documents import (
    KnowledgeDocumentStatus,
    RawDocument,
)

# 导入向量接口和批量结果。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
)

# uuid4 用来生成一个数据库中不存在的用户 ID。
from uuid import uuid4

import pytest

# func 和 select 用来统计数据库记录数量。
from sqlalchemy import (
    func,
    select,
)

# 导入完整知识入库函数。
from ticketpilot.knowledge.ingestion import (
    KnowledgeIngestionError,
    ingest_document,
)

# 导入需要从数据库重新查询的 ORM 类型。
from ticketpilot.knowledge.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)


# 这个函数负责生成集成测试使用的 1024 维向量。
def build_test_vector(
    value: float,
) -> tuple[float, ...]:
    return tuple(
        [value] * EMBEDDING_DIMENSIONS
    )

# 这个测试负责验证完整入库结果能够从真实数据库重新查询。
def test_ingest_document_persists_document_and_chunks(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    # 先提交 fixture 创建的用户。
    # 这样 created_by 外键能够指向真实用户。
    database_session.commit()

    # 创建模拟向量模型。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 35 个字符会被切分成两个片段，
    # 因此模拟模型返回两个向量。
    embedding_provider.embed_texts.return_value = (
        EmbeddingBatch(
            model_name="fake-embedding-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=(
                build_test_vector(0.1),
                build_test_vector(0.2),
            ),
        )
    )

    # 标题包含全角字符，
    # 可以同时验证真实清洗流程。
    raw_document = RawDocument(
        title="ＴｉｃｋｅｔＰｉｌｏｔ 入库测试",
        content="知识库内容" * 7,
    )

    # 执行完整入库流程。
    saved_document = ingest_document(
        session=database_session,
        raw_document=raw_document,
        created_by=database_user.id,
        embedding_provider=(
            embedding_provider
        ),
        max_characters=20,
        overlap_characters=5,
    )

    # 保存主键，供重新查询使用。
    document_id = saved_document.id

    # 清除 Session 中已经缓存的 ORM 数据。
    # 后面的 get() 必须重新读取数据库。
    database_session.expire_all()

    # 根据主键重新查询文档。
    stored_document = database_session.get(
        KnowledgeDocumentRecord,
        document_id,
    )

    # 数据库中必须存在这篇文档。
    assert stored_document is not None

    # 全角字符应该已经被清洗成半角。
    assert (
        stored_document.title
        == "TicketPilot 入库测试"
    )

    # 文档状态应该是可以参与检索的 ready。
    assert stored_document.status == (
        KnowledgeDocumentStatus.READY.value
    )

    # 数据库应该保存模型名称和维度。
    assert (
        stored_document.embedding_model
        == "fake-embedding-model"
    )
    assert (
        stored_document.embedding_dimensions
        == EMBEDDING_DIMENSIONS
    )

    # created_by 应该指向 fixture 创建的真实用户。
    assert (
        stored_document.created_by
        == database_user.id
    )

    # 数据库中应该保存两个知识片段。
    assert len(
        stored_document.chunks
    ) == 2

    # 片段必须按照 chunk_index 排列。
    assert [
        chunk.chunk_index
        for chunk in stored_document.chunks
    ] == [
        0,
        1,
    ]

    # 两个片段都应该关联到当前文档。
    assert all(
        chunk.document_id == document_id
        for chunk in stored_document.chunks
    )

    # SQLite 会把测试向量保存成 JSON list。
    assert isinstance(
        stored_document.chunks[0].embedding,
        list,
    )

    # 向量长度必须保持为 1024。
    assert len(
        stored_document.chunks[0].embedding
    ) == EMBEDDING_DIMENSIONS

    # 对应片段应该保存正确的向量值。
    assert (
        stored_document.chunks[0].embedding[0]
        == 0.1
    )
    assert (
        stored_document.chunks[1].embedding[0]
        == 0.2
    )

# 这个测试负责验证数据库失败后不会残留文档或片段。
def test_ingest_document_rolls_back_all_records_on_failure(
    database_session: Session,
) -> None:
    # 创建模拟向量模型。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    embedding_provider.embed_texts.return_value = (
        EmbeddingBatch(
            model_name="fake-embedding-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=(
                build_test_vector(0.1),
            ),
        )
    )

    # 这个用户 ID 没有保存到 users 表。
    missing_user_id = str(
        uuid4()
    )

    # created_by 外键最终会因为用户不存在而失败。
    with pytest.raises(
        KnowledgeIngestionError,
        match="知识文档入库失败",
    ):
        ingest_document(
            session=database_session,
            raw_document=RawDocument(
                title="外键失败测试",
                content="这是一篇有效的知识文档正文。",
            ),
            created_by=missing_user_id,
            embedding_provider=(
                embedding_provider
            ),
        )

    # rollback 后不能残留文档记录。
    document_count = database_session.scalar(
        select(
            func.count(
                KnowledgeDocumentRecord.id
            )
        )
    )
    assert document_count == 0

    # rollback 后也不能残留知识片段。
    chunk_count = database_session.scalar(
        select(
            func.count(
                KnowledgeChunkRecord.id
            )
        )
    )
    assert chunk_count == 0