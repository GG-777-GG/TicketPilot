"""
测试知识文档完整入库服务。

成功流程：
RawDocument
→ 清洗和切块
→ 生成向量
→ 创建文档与片段 ORM 对象
→ Repository 写入
→ commit

失败流程：
任意步骤发生异常
→ rollback
→ KnowledgeIngestionError
"""

# MagicMock 用来模拟数据库 Session 和 Embedding Provider。
from unittest.mock import MagicMock

# pytest 用来验证预期异常。
import pytest

# SQLAlchemyError 用来模拟数据库错误。
from sqlalchemy.exc import SQLAlchemyError

# Session 用作数据库模拟对象的接口规范。
from sqlalchemy.orm import Session

# 导入 Repository 转换后的数据库业务异常。
from ticketpilot.knowledge.repository import (
    KnowledgeRepositoryError,
)

# 导入数据库固定使用的向量维度。
from ticketpilot.knowledge.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入原始文档和文档状态。
from ticketpilot.knowledge.documents import (
    KnowledgeDocumentStatus,
    RawDocument,
)

# 导入向量接口、结果对象和异常。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
    EmbeddingProviderError,
    InvalidEmbeddingResponseError,
)

# 导入需要测试的入库函数和统一异常。
from ticketpilot.knowledge.ingestion import (
    KnowledgeIngestionError,
    ingest_document,
)

# 导入最终应该创建的文档 ORM 类型。
from ticketpilot.knowledge.models import (
    KnowledgeDocumentRecord,
)


# 这个函数负责生成测试使用的 1024 维向量。
def build_test_vector(
    value: float,
) -> tuple[float, ...]:
    return tuple(
        [value] * EMBEDDING_DIMENSIONS
    )

# 这个测试负责验证文档能够完整入库并提交事务。
def test_ingest_document_saves_document_and_chunks() -> None:
    # 创建模拟数据库 Session。
    session = MagicMock(
        spec=Session
    )

    # 创建模拟的向量模型。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 模拟模型返回两个有效的 1024 维向量。
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

    # 35 个字符使用最大 20、重叠 5 的规则，
    # 最终会被切分成两个知识片段。
    raw_document = RawDocument(
        title="知识库入库测试",
        content="知识库内容" * 7,
    )

    # 执行完整入库服务。
    saved_document = ingest_document(
        session=session,
        raw_document=raw_document,
        created_by="user-001",
        embedding_provider=(
            embedding_provider
        ),
        max_characters=20,
        overlap_characters=5,
    )

    # 返回值应该是一篇文档 ORM 对象。
    assert isinstance(
        saved_document,
        KnowledgeDocumentRecord,
    )

    # 文档应该已经达到可检索状态。
    assert saved_document.status == (
        KnowledgeDocumentStatus.READY.value
    )

    # 文档应该记录实际使用的向量模型和维度。
    assert (
        saved_document.embedding_model
        == "fake-embedding-model"
    )
    assert (
        saved_document.embedding_dimensions
        == EMBEDDING_DIMENSIONS
    )

    # 文档应该记录创建者。
    assert (
        saved_document.created_by
        == "user-001"
    )

    # 本次应该创建两个知识片段。
    assert len(
        saved_document.chunks
    ) == 2

    # 两个片段应该分别保存对应的向量。
    assert (
        saved_document.chunks[0].embedding
        == list(build_test_vector(0.1))
    )
    assert (
        saved_document.chunks[1].embedding
        == list(build_test_vector(0.2))
    )

    # 向量模型应该只接收到一次批量请求。
    embedding_provider.embed_texts.assert_called_once()

    # Repository 应该把整篇文档加入 Session 并执行 flush。
    session.add.assert_called_once_with(
        saved_document
    )
    session.flush.assert_called_once_with()

    # 所有步骤成功后必须提交事务。
    session.commit.assert_called_once_with()

    # 成功流程不能执行回滚。
    session.rollback.assert_not_called()

# 这个测试负责验证向量生成失败时会回滚事务。
def test_ingest_document_rolls_back_when_embedding_fails() -> None:
    # 创建模拟数据库 Session。
    session = MagicMock(
        spec=Session
    )

    # 创建模拟向量模型。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # side_effect 表示调用 embed_texts() 时直接抛出异常。
    embedding_provider.embed_texts.side_effect = (
        EmbeddingProviderError(
            "模拟向量服务不可用"
        )
    )

    # 准备一篇有效的原始文档。
    raw_document = RawDocument(
        title="向量失败测试",
        content="这是一篇有效的知识文档正文。",
    )

    # 入库服务应该把底层异常转换成统一异常。
    with pytest.raises(
        KnowledgeIngestionError,
        match="知识文档入库失败",
    ) as exception_info:
        ingest_document(
            session=session,
            raw_document=raw_document,
            created_by="user-001",
            embedding_provider=(
                embedding_provider
            ),
        )

    # 统一异常应该保留原来的向量服务异常。
    assert isinstance(
        exception_info.value.__cause__,
        EmbeddingProviderError,
    )

    # 向量生成失败发生在数据库写入之前。
    session.add.assert_not_called()
    session.flush.assert_not_called()

    # 失败流程绝对不能提交事务。
    session.commit.assert_not_called()

    # 失败后必须回滚当前 Session。
    session.rollback.assert_called_once_with()

# 这个测试负责验证数据库写入失败时会回滚事务。
def test_ingest_document_rolls_back_when_database_write_fails() -> None:
    # 创建模拟数据库 Session。
    session = MagicMock(
        spec=Session
    )

    # 模拟 flush() 执行 SQL 时发生数据库错误。
    session.flush.side_effect = SQLAlchemyError(
        "模拟数据库写入失败"
    )

    # 创建模拟向量模型。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 当前短文档只会生成一个片段，
    # 因此模型返回一个向量。
    embedding_provider.embed_texts.return_value = (
        EmbeddingBatch(
            model_name="fake-embedding-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=(
                build_test_vector(0.1),
            ),
        )
    )

    # 准备一篇有效文档。
    raw_document = RawDocument(
        title="数据库失败测试",
        content="这是一篇有效的知识文档正文。",
    )

    # 数据库异常最终应该转换成统一入库异常。
    with pytest.raises(
        KnowledgeIngestionError,
        match="知识文档入库失败",
    ) as exception_info:
        ingest_document(
            session=session,
            raw_document=raw_document,
            created_by="user-001",
            embedding_provider=(
                embedding_provider
            ),
        )

    # Ingestion Service 的直接原因是 Repository 异常。
    assert isinstance(
        exception_info.value.__cause__,
        KnowledgeRepositoryError,
    )

    # Repository 异常内部仍然保留原始数据库异常。
    assert isinstance(
        exception_info.value.__cause__.__cause__,
        SQLAlchemyError,
    )

    # 文档已经尝试加入 Session。
    session.add.assert_called_once()

    # flush 已经尝试执行 SQL。
    session.flush.assert_called_once_with()

    # 数据库写入失败后不能提交。
    session.commit.assert_not_called()

    # 整个事务必须回滚。
    session.rollback.assert_called_once_with()

# 这个测试负责验证事务提交失败时仍然会执行回滚。
def test_ingest_document_rolls_back_when_commit_fails() -> None:
    # 创建模拟数据库 Session。
    session = MagicMock(
        spec=Session
    )

    # 模拟最终提交事务时发生数据库错误。
    session.commit.side_effect = SQLAlchemyError(
        "模拟数据库提交失败"
    )

    # 创建模拟向量模型。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 当前文档只会生成一个知识片段。
    embedding_provider.embed_texts.return_value = (
        EmbeddingBatch(
            model_name="fake-embedding-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=(
                build_test_vector(0.1),
            ),
        )
    )

    # 准备有效的原始文档。
    raw_document = RawDocument(
        title="事务提交失败测试",
        content="这是一篇有效的知识文档正文。",
    )

    # commit 异常应该转换成统一入库异常。
    with pytest.raises(
        KnowledgeIngestionError,
        match="知识文档入库失败",
    ) as exception_info:
        ingest_document(
            session=session,
            raw_document=raw_document,
            created_by="user-001",
            embedding_provider=(
                embedding_provider
            ),
        )

    # commit 是 Service 直接调用的，
    # 因此统一异常的直接原因是 SQLAlchemyError。
    assert isinstance(
        exception_info.value.__cause__,
        SQLAlchemyError,
    )

    # add 和 flush 应该已经成功执行。
    session.add.assert_called_once()
    session.flush.assert_called_once_with()

    # commit 已经尝试执行，但发生了异常。
    session.commit.assert_called_once_with()

    # commit 失败后仍然必须回滚事务。
    session.rollback.assert_called_once_with()

@pytest.mark.parametrize(
    (
        "invalid_batch",
        "expected_message",
    ),
    [
        # 一段文本却没有返回任何向量。
        (
            EmbeddingBatch(
                model_name="fake-model",
                dimensions=EMBEDDING_DIMENSIONS,
                vectors=(),
            ),
            "向量数量",
        ),

        # 模型声明只返回 2 维向量，
        # 但数据库结构固定要求 1024 维。
        (
            EmbeddingBatch(
                model_name="fake-model",
                dimensions=2,
                vectors=(
                    (0.1, 0.2),
                ),
            ),
            "向量维度",
        ),

        # 声明是 1024 维，
        # 但实际向量中只有一个数值。
        (
            EmbeddingBatch(
                model_name="fake-model",
                dimensions=EMBEDDING_DIMENSIONS,
                vectors=(
                    (0.1,),
                ),
            ),
            "实际向量维度",
        ),
    ],
)
# 这个测试负责验证入库服务会拒绝不合格的向量结果。
def test_ingest_document_rejects_invalid_embedding_batch(
    invalid_batch: EmbeddingBatch,
    expected_message: str,
) -> None:
    # 创建模拟数据库 Session。
    session = MagicMock(
        spec=Session
    )

    # 创建模拟向量模型，并返回当前测试参数。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    embedding_provider.embed_texts.return_value = (
        invalid_batch
    )

    # 准备只会产生一个片段的有效文档。
    raw_document = RawDocument(
        title="错误向量测试",
        content="这是一篇有效的知识文档正文。",
    )

    # 不合格的向量结果应该转换成统一入库异常。
    with pytest.raises(
        KnowledgeIngestionError,
        match="知识文档入库失败",
    ) as exception_info:
        ingest_document(
            session=session,
            raw_document=raw_document,
            created_by="user-001",
            embedding_provider=(
                embedding_provider
            ),
        )

    # 真正的底层原因应该是向量响应不合格。
    assert isinstance(
        exception_info.value.__cause__,
        InvalidEmbeddingResponseError,
    )

    # 错误信息应该对应当前失败原因。
    assert expected_message in str(
        exception_info.value.__cause__
    )

    # 向量检查没有通过，不能尝试写入数据库。
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()

    # 失败流程仍然要清理当前事务。
    session.rollback.assert_called_once_with()