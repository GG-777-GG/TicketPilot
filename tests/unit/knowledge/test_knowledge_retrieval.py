"""
测试知识检索服务。

主要验证：
- 用户问题能够转换成向量。
- Repository 能收到正确的检索参数。
- 数据库结果能转换成 RetrievedKnowledgeChunk。
- 非法参数会被拒绝。
- 向量服务和数据库异常会被统一转换。
"""

# MagicMock 用来模拟 Session 和 Embedding Provider。
from unittest.mock import MagicMock

# pytest 提供异常验证、参数化测试和 monkeypatch。
import pytest

# Session 用作模拟数据库对象的接口规范。
from sqlalchemy.orm import Session

# 导入数据库固定要求的向量维度。
from ticketpilot.knowledge.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入知识来源枚举。
from ticketpilot.knowledge.documents import (
    KnowledgeSourceType,
)

# 导入向量接口、向量结果和向量异常。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
    EmbeddingProviderError,
    InvalidEmbeddingResponseError,
)

# 导入 Repository 查询结果类型和异常。
from ticketpilot.knowledge.repository import (
    KnowledgeChunkSearchRow,
    KnowledgeRepositoryError,
)

# 导入需要测试的检索函数、结果类型和异常。
from ticketpilot.knowledge.retrieval import (
    MAX_RETRIEVAL_TOP_K,
    InvalidKnowledgeQueryError,
    KnowledgeRetrievalError,
    RetrievedKnowledgeChunk,
    retrieve_knowledge,
)


# 这个函数负责生成测试使用的 1024 维向量。
def build_test_vector(
    value: float = 0.1,
) -> tuple[
    float,
    ...,
]:
    return tuple(
        [value] * EMBEDDING_DIMENSIONS
    )

# 这个测试负责验证知识检索的完整成功流程。
def test_retrieve_knowledge_returns_matching_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 创建模拟数据库 Session。
    session = MagicMock(
        spec=Session
    )

    # 创建模拟向量模型。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 准备用户问题对应的 1024 维测试向量。
    query_vector = build_test_vector(
        0.1
    )

    # 模拟向量模型返回一个有效的问题向量。
    embedding_provider.embed_texts.return_value = (
        EmbeddingBatch(
            model_name="fake-embedding-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=(
                query_vector,
            ),
        )
    )

    # 模拟 Repository 从数据库查询到一条知识片段。
    repository_row = KnowledgeChunkSearchRow(
        chunk_id="chunk-001",
        document_id="document-001",
        document_title="账号使用说明",
        document_version=1,
        source_type=(
            KnowledgeSourceType.MANUAL.value
        ),
        source_uri=None,
        chunk_index=0,
        content="用户可以通过注册邮箱重置密码。",
        start_offset=0,
        end_offset=17,
        cosine_distance=0.25,
    )

    # 创建模拟的 Repository 查询函数。
    repository_search = MagicMock(
        return_value=(
            repository_row,
        )
    )

    # 临时替换 Retrieval Service 中真正的数据库查询函数。
    monkeypatch.setattr(
        "ticketpilot.knowledge.retrieval."
        "search_ready_chunks_by_cosine",
        repository_search,
    )

    # 执行需要测试的知识检索函数。
    results = retrieve_knowledge(
        session=session,

        # 故意保留前后空格，验证 strip() 是否生效。
        question="  如何重置密码？  ",

        embedding_provider=embedding_provider,
        top_k=3,
        min_similarity=0.5,
    )

    # 向量模型应该收到清理过的问题。
    embedding_provider.embed_texts.assert_called_once_with(
        [
            "如何重置密码？",
        ]
    )

    # Repository 应该收到正确的查询向量和过滤条件。
    repository_search.assert_called_once_with(
        session,
        query_embedding=query_vector,
        embedding_model="fake-embedding-model",
        embedding_dimensions=(
            EMBEDDING_DIMENSIONS
        ),
        top_k=3,
        min_similarity=0.5,
    )

    # Service 应该返回 tuple。
    assert isinstance(
        results,
        tuple,
    )

    # 当前模拟数据库只返回了一条结果。
    assert len(results) == 1

    # 数据库查询结果应该已经转换成业务结果对象。
    result = results[0]

    assert isinstance(
        result,
        RetrievedKnowledgeChunk,
    )

    assert result.chunk_id == "chunk-001"
    assert result.document_id == "document-001"
    assert result.document_title == "账号使用说明"

    # 数据库中的字符串应该转换成来源枚举。
    assert (
        result.source_type
        is KnowledgeSourceType.MANUAL
    )

    assert result.content == (
        "用户可以通过注册邮箱重置密码。"
    )

    # similarity = 1 - cosine_distance
    assert result.similarity == pytest.approx(
        0.75
    )

    # 成功查询不需要提交或者回滚事务。
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "question",
    [
        "",
        "   ",
        "\n\t",
    ],
)
# 这个测试负责验证空白问题会被拒绝。
def test_retrieve_knowledge_rejects_blank_question(
    question: str,
) -> None:
    # 创建模拟数据库 Session。
    session = MagicMock(
        spec=Session
    )

    # 创建模拟向量模型。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 空白问题应该抛出参数异常。
    with pytest.raises(
        InvalidKnowledgeQueryError,
        match="知识查询问题不能为空",
    ):
        retrieve_knowledge(
            session=session,
            question=question,
            embedding_provider=(
                embedding_provider
            ),
        )

    # 参数验证失败后，不能调用向量模型。
    embedding_provider.embed_texts.assert_not_called()

    # 也不能操作数据库事务。
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "top_k",
    [
        0,
        MAX_RETRIEVAL_TOP_K + 1,
    ],
)
# 这个测试负责验证 top_k 必须在允许范围内。
def test_retrieve_knowledge_rejects_invalid_top_k(
    top_k: int,
) -> None:
    session = MagicMock(
        spec=Session
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    with pytest.raises(
        InvalidKnowledgeQueryError,
        match="top_k 必须在 1 到 20 之间",
    ):
        retrieve_knowledge(
            session=session,
            question="如何重置密码？",
            embedding_provider=(
                embedding_provider
            ),
            top_k=top_k,
        )

    # 非法 top_k 应该在调用模型之前被发现。
    embedding_provider.embed_texts.assert_not_called()


@pytest.mark.parametrize(
    "min_similarity",
    [
        -0.01,
        1.01,
    ],
)
# 这个测试负责验证最低相似度必须在 0 到 1 之间。
def test_retrieve_knowledge_rejects_invalid_similarity(
    min_similarity: float,
) -> None:
    session = MagicMock(
        spec=Session
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    with pytest.raises(
        InvalidKnowledgeQueryError,
        match="min_similarity 必须在 0 到 1 之间",
    ):
        retrieve_knowledge(
            session=session,
            question="如何重置密码？",
            embedding_provider=(
                embedding_provider
            ),
            min_similarity=min_similarity,
        )

    # 非法相似度不能继续生成问题向量。
    embedding_provider.embed_texts.assert_not_called()

# 这个测试负责验证向量模型调用失败时会转换异常。
def test_retrieve_knowledge_converts_embedding_failure() -> None:
    session = MagicMock(
        spec=Session
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 模拟 Ollama 或其他向量服务调用失败。
    embedding_provider.embed_texts.side_effect = (
        EmbeddingProviderError(
            "模拟向量服务不可用"
        )
    )

    # 底层向量异常应该转换成统一的知识检索异常。
    with pytest.raises(
        KnowledgeRetrievalError,
        match="知识查询向量生成失败",
    ) as exception_info:
        retrieve_knowledge(
            session=session,
            question="如何重置密码？",
            embedding_provider=(
                embedding_provider
            ),
        )

    # from error 应该保留真正的底层异常。
    assert isinstance(
        exception_info.value.__cause__,
        EmbeddingProviderError,
    )

    # 生成问题向量失败时，还没有访问数据库。
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    (
        "invalid_batch",
        "expected_message",
    ),
    [
        # 输入一个问题，却没有返回向量。
        (
            EmbeddingBatch(
                model_name="fake-model",
                dimensions=EMBEDDING_DIMENSIONS,
                vectors=(),
            ),
            "问题向量数量必须是 1",
        ),

        # 模型声明返回 2 维，而数据库要求 1024 维。
        (
            EmbeddingBatch(
                model_name="fake-model",
                dimensions=2,
                vectors=(
                    (
                        0.1,
                        0.2,
                    ),
                ),
            ),
            "问题向量维度与数据库结构不一致",
        ),

        # 声明返回 1024 维，但实际只有一个数值。
        (
            EmbeddingBatch(
                model_name="fake-model",
                dimensions=EMBEDDING_DIMENSIONS,
                vectors=(
                    (
                        0.1,
                    ),
                ),
            ),
            "实际问题向量维度与声明维度不一致",
        ),
    ],
)
# 这个测试负责验证检索服务会拒绝不合格的问题向量。
def test_retrieve_knowledge_rejects_invalid_embedding_batch(
    invalid_batch: EmbeddingBatch,
    expected_message: str,
) -> None:
    session = MagicMock(
        spec=Session
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 让模拟模型返回当前这组不合格数据。
    embedding_provider.embed_texts.return_value = (
        invalid_batch
    )

    with pytest.raises(
        KnowledgeRetrievalError,
        match="知识查询向量生成失败",
    ) as exception_info:
        retrieve_knowledge(
            session=session,
            question="如何重置密码？",
            embedding_provider=(
                embedding_provider
            ),
        )

    # 统一异常内部应该保留向量结果异常。
    cause = exception_info.value.__cause__

    assert isinstance(
        cause,
        InvalidEmbeddingResponseError,
    )

    # 检查失败原因是否与当前测试数据对应。
    assert expected_message in str(
        cause
    )

    # 向量没有通过验证，不能访问数据库。
    session.commit.assert_not_called()
    session.rollback.assert_not_called()

# 这个函数负责创建能够正常返回问题向量的模拟模型。
def build_valid_embedding_provider() -> MagicMock:
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    embedding_provider.embed_texts.return_value = (
        EmbeddingBatch(
            model_name="fake-embedding-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=(
                build_test_vector(),
            ),
        )
    )

    return embedding_provider


# 这个测试负责验证数据库查询失败时会回滚事务。
def test_retrieve_knowledge_rolls_back_when_repository_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    embedding_provider = (
        build_valid_embedding_provider()
    )

    # 模拟 Repository 查询数据库失败。
    repository_search = MagicMock(
        side_effect=KnowledgeRepositoryError(
            "模拟数据库查询失败"
        )
    )

    monkeypatch.setattr(
        "ticketpilot.knowledge.retrieval."
        "search_ready_chunks_by_cosine",
        repository_search,
    )

    with pytest.raises(
        KnowledgeRetrievalError,
        match="知识片段检索失败",
    ) as exception_info:
        retrieve_knowledge(
            session=session,
            question="如何重置密码？",
            embedding_provider=(
                embedding_provider
            ),
        )

    # 统一异常应该保留 Repository 异常。
    assert isinstance(
        exception_info.value.__cause__,
        KnowledgeRepositoryError,
    )

    # 查询失败后需要清理数据库事务。
    session.rollback.assert_called_once_with()

    # 查询操作不需要提交。
    session.commit.assert_not_called()


# 这个测试负责验证没有匹配结果时返回空元组。
def test_retrieve_knowledge_returns_empty_tuple_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    embedding_provider = (
        build_valid_embedding_provider()
    )

    # 模拟数据库没有找到符合条件的知识片段。
    repository_search = MagicMock(
        return_value=()
    )

    monkeypatch.setattr(
        "ticketpilot.knowledge.retrieval."
        "search_ready_chunks_by_cosine",
        repository_search,
    )

    results = retrieve_knowledge(
        session=session,
        question="一个知识库中不存在的问题",
        embedding_provider=embedding_provider,
    )

    # 没有结果不是系统错误，应该正常返回空元组。
    assert results == ()

    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责验证数据库中的非法来源类型会被拒绝。
def test_retrieve_knowledge_rejects_invalid_source_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    embedding_provider = (
        build_valid_embedding_provider()
    )

    # 模拟数据库返回一条来源类型错误的数据。
    invalid_row = KnowledgeChunkSearchRow(
        chunk_id="chunk-001",
        document_id="document-001",
        document_title="错误来源测试",
        document_version=1,

        # 系统只允许 manual、file 或 url。
        source_type="unsupported",

        source_uri=None,
        chunk_index=0,
        content="测试知识片段",
        start_offset=0,
        end_offset=6,
        cosine_distance=0.2,
    )

    monkeypatch.setattr(
        "ticketpilot.knowledge.retrieval."
        "search_ready_chunks_by_cosine",
        MagicMock(
            return_value=(
                invalid_row,
            )
        ),
    )

    with pytest.raises(
        KnowledgeRetrievalError,
        match="知识检索结果包含无效数据",
    ) as exception_info:
        retrieve_knowledge(
            session=session,
            question="测试问题",
            embedding_provider=(
                embedding_provider
            ),
        )

    # KnowledgeSourceType("unsupported")
    # 应该产生 ValueError。
    assert isinstance(
        exception_info.value.__cause__,
        ValueError,
    )

    # 数据库查询已经成功，所以不需要回滚。
    session.rollback.assert_not_called()