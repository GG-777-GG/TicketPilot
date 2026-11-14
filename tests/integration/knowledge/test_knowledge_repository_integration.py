"""
使用真实临时数据库测试知识库 Repository。

基础设施分层：
- tests/conftest.py 提供 database_session 和 database_user。
- tests/factories/knowledge.py 构造文档和片段。
- 本文件只描述事务场景并断言业务结果。

测试流程：
提交测试用户
→ factory 构造文档和片段
→ add_document() 执行 flush
→ 当前事务能够查询到文档和片段
→ rollback()
→ 文档和片段一起消失
→ 已提交的用户仍然存在
"""

# UTC 和 datetime 用来固定测试文档的创建时间。
from datetime import (
    UTC,
    datetime,
)

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
    build_knowledge_document_version,
)
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentVersionRecord,
)
from ticketpilot.knowledge.persistence.repository import (
    add_document,
    get_document_version_by_number,
    get_document_by_id,
    get_document_record_by_id,
    list_document_versions,
    list_documents,
    replace_document_chunks,
    search_ready_chunks_by_cosine,
)



import pytest

from tests.factories.identity import (
    build_user,
)
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
)


def test_add_document_and_chunks_can_be_rolled_back(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    # 先提交公共 fixture 创建的用户，建立旧事务基线。
    database_session.commit()

    document_content = (
        "用户忘记密码时，可以通过注册邮箱重置密码。"
    )
    document = build_knowledge_document(
        created_by=database_user.id,
        raw_content=document_content,
    )
    chunk = build_knowledge_chunk(
        content=document_content,
    )
    document.chunks.append(chunk)

    saved_document = add_document(
        database_session,
        document,
    )

    assert saved_document is document
    assert database_session.scalar(
        select(
            func.count(KnowledgeDocumentRecord.id)
        )
    ) == 1
    assert database_session.scalar(
        select(
            func.count(KnowledgeChunkRecord.id)
        )
    ) == 1

    # Repository 只 flush、没有 commit，所以这里可以整体撤销。
    database_session.rollback()

    assert database_session.scalar(
        select(
            func.count(KnowledgeDocumentRecord.id)
        )
    ) == 0
    assert database_session.scalar(
        select(
            func.count(KnowledgeChunkRecord.id)
        )
    ) == 0

    # 旧事务中已经提交的用户不受本次 rollback 影响。
    assert database_session.scalar(
        select(func.count(UserRecord.id))
    ) == 1
# 验证没有知识文档时返回空分页结果。
def test_list_documents_returns_empty_page(
    database_session: Session,
) -> None:
    # 查询空数据库的第一页。
    page = list_documents(
        database_session,
        limit=20,
        offset=0,
    )

    # 当前页不应该包含任何文档。
    assert page.items == ()

    # 全部文档数量应该是 0。
    assert page.total == 0

# 验证知识文档列表的排序、分页和片段数量。
def test_list_documents_returns_paginated_summaries(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    # 创建最早的一篇文档。
    oldest_document = build_knowledge_document(
        created_by=database_user.id,
        document_id="document-001",
        title="最早的文档",
        raw_content="最早文档的正文",
    )
    oldest_document.created_at = datetime(
        2026,
        1,
        1,
        tzinfo=UTC,
    )

    # 最早文档包含一个片段。
    oldest_document.chunks.append(
        build_knowledge_chunk(
            chunk_index=0,
            content="最早文档的片段",
        )
    )

    # 创建时间位于中间的文档。
    middle_document = build_knowledge_document(
        created_by=database_user.id,
        document_id="document-002",
        title="中间的文档",
        raw_content="中间文档的正文",
        visibility="restricted",
    )
    middle_document.created_at = datetime(
        2026,
        1,
        2,
        tzinfo=UTC,
    )

    # 中间文档包含两个片段。
    middle_document.chunks.extend(
        [
            build_knowledge_chunk(
                chunk_index=0,
                content="中间文档的第一个片段",
            ),
            build_knowledge_chunk(
                chunk_index=1,
                content="中间文档的第二个片段",
            ),
        ]
    )

    # 创建最新的一篇待处理文档。
    newest_document = build_knowledge_document(
        created_by=database_user.id,
        document_id="document-003",
        title="最新的文档",
        raw_content="最新文档的正文",
    )
    newest_document.created_at = datetime(
        2026,
        1,
        3,
        tzinfo=UTC,
    )

    # 最新文档故意不添加片段，
    # 用于验证 chunk_count 可以是 0。

    # 将三篇文档写入临时数据库。
    database_session.add_all(
        [
            oldest_document,
            middle_document,
            newest_document,
        ]
    )
    database_session.flush()

    # 第一页最多返回两篇文档。
    first_page = list_documents(
        database_session,
        limit=2,
        offset=0,
    )

    # total 是全部数量，不受 limit 影响。
    assert first_page.total == 3

    # 最新创建的文档应该排在最前面。
    assert [
        item.title
        for item in first_page.items
    ] == [
        "最新的文档",
        "中间的文档",
    ]

    # 两篇文档的片段数量应该分别是 0 和 2。
    assert [
        item.chunk_count
        for item in first_page.items
    ] == [
        0,
        2,
    ]

    # 列表摘要必须返回数据库中的真实可见性。
    assert [item.visibility for item in first_page.items] == [
        "internal",
        "restricted",
    ]

    # 列表摘要不应该携带大段正文。
    assert not hasattr(
        first_page.items[0],
        "raw_content",
    )
    assert not hasattr(
        first_page.items[0],
        "cleaned_content",
    )

    # 查询第二页，跳过前两篇。
    second_page = list_documents(
        database_session,
        limit=2,
        offset=2,
    )

    # 第二页只剩最早的文档。
    assert len(second_page.items) == 1
    assert (
        second_page.items[0].title
        == "最早的文档"
    )
    assert (
        second_page.items[0].chunk_count
        == 1
    )

    # 不同页面返回的 total 应该保持一致。
    assert second_page.total == 3

# 验证可以根据 ID 查询单篇知识文档详情。
def test_get_document_by_id_returns_document_detail(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    # 创建一篇保留原始空格、同时具有清洗后正文的文档。
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="document-detail-001",
        title="员工密码重置流程",
        raw_content=(
            "  员工可以通过企业邮箱重置密码。  "
        ),
        cleaned_content=(
            "员工可以通过企业邮箱重置密码。"
        ),
        visibility="restricted",
    )

    # 添加一个知识片段，用于验证数据库 COUNT 结果。
    document.chunks.append(
        build_knowledge_chunk(
            chunk_index=0,
            content=(
                "员工可以通过企业邮箱重置密码。"
            ),
        )
    )

    database_session.add(document)
    database_session.flush()

    detail = get_document_by_id(
        database_session,
        "document-detail-001",
    )

    assert detail is not None
    assert detail.id == "document-detail-001"
    assert detail.title == "员工密码重置流程"
    assert detail.raw_content == (
        "  员工可以通过企业邮箱重置密码。  "
    )
    assert detail.cleaned_content == (
        "员工可以通过企业邮箱重置密码。"
    )
    assert detail.chunk_count == 1
    assert detail.visibility == "restricted"

    # 详情读模型不应携带 ORM 关系或向量数据。
    assert not hasattr(detail, "chunks")
    assert not hasattr(detail, "embedding")


# 验证不存在的文档 ID 会返回 None。
def test_get_document_by_id_returns_none_when_missing(
    database_session: Session,
) -> None:
    detail = get_document_by_id(
        database_session,
        "missing-document",
    )

    assert detail is None


# 验证可以取得由当前 Session 管理的文档 ORM 对象。
def test_get_document_record_by_id_returns_managed_record(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="document-record-001",
        title="等待归档的文档",
        raw_content="这是一篇等待归档的知识文档。",
    )
    database_session.add(document)
    database_session.flush()

    found_document = get_document_record_by_id(
        database_session,
        "document-record-001",
    )

    # session.get() 返回真正的 ORM 对象，而不是只读详情对象。
    assert found_document is document
    assert found_document.status == (
        KnowledgeDocumentStatus.PENDING.value
    )


# 验证找不到可修改的文档记录时返回 None。
def test_get_document_record_by_id_returns_none_when_missing(
    database_session: Session,
) -> None:
    document = get_document_record_by_id(
        database_session,
        "missing-document-record",
    )

    assert document is None


# 验证新片段能够完整替换数据库中的旧片段。
def test_replace_document_chunks_replaces_persisted_chunks(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="replace-chunks-document",
    )
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="old-chunk",
            chunk_index=0,
            content="旧知识片段",
        )
    )
    database_session.add(document)
    database_session.commit()

    replace_document_chunks(
        database_session,
        document,
        [
            build_knowledge_chunk(
                chunk_id="new-chunk-001",
                chunk_index=0,
                content="第一个新知识片段",
            ),
            build_knowledge_chunk(
                chunk_id="new-chunk-002",
                chunk_index=1,
                content="第二个新知识片段",
            ),
        ],
    )
    database_session.commit()
    database_session.expire_all()

    saved_chunks = database_session.scalars(
        select(
            KnowledgeChunkRecord
        )
        .where(
            KnowledgeChunkRecord.document_id
            == "replace-chunks-document"
        )
        .order_by(
            KnowledgeChunkRecord.chunk_index
        )
    ).all()

    assert [
        chunk.id
        for chunk in saved_chunks
    ] == [
        "new-chunk-001",
        "new-chunk-002",
    ]


# 验证替换后回滚会恢复原来的知识片段。
def test_replace_document_chunks_can_be_rolled_back(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="rollback-chunks-document",
    )
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="original-chunk",
            chunk_index=0,
            content="必须能够恢复的原始片段",
        )
    )
    database_session.add(document)
    database_session.commit()

    replace_document_chunks(
        database_session,
        document,
        [
            build_knowledge_chunk(
                chunk_id="temporary-new-chunk",
                chunk_index=0,
                content="事务失败后不能保留的临时片段",
            ),
        ],
    )

    # 模拟后续更新步骤失败，撤销整个片段替换事务。
    database_session.rollback()
    database_session.expire_all()

    saved_chunks = database_session.scalars(
        select(
            KnowledgeChunkRecord
        ).where(
            KnowledgeChunkRecord.document_id
            == "rollback-chunks-document"
        )
    ).all()

    assert len(saved_chunks) == 1
    assert saved_chunks[0].id == "original-chunk"
    assert saved_chunks[0].content == (
        "必须能够恢复的原始片段"
    )


# 验证能够根据文档 ID 和版本号读取完整历史内容。
def test_get_document_version_by_number_returns_detail(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        document_id="document-version-detail",
        created_by=database_user.id,
        title="当前第 2 版",
    )
    document.version = 2
    database_session.add(
        document
    )
    database_session.flush()

    history = build_knowledge_document_version(
        version_id="history-detail-version-1",
        document_id=document.id,
        version=1,
        title="历史第 1 版",
        raw_content="  用户最初提交的历史正文。  ",
        cleaned_content="用户最初提交的历史正文。",
        created_by=database_user.id,
        superseded_by=database_user.id,
    )
    database_session.add(
        history
    )
    database_session.commit()

    detail = get_document_version_by_number(
        database_session,
        document.id,
        1,
    )

    assert detail is not None
    assert detail.document_id == document.id
    assert detail.version == 1
    assert detail.title == "历史第 1 版"
    assert detail.raw_content == (
        "  用户最初提交的历史正文。  "
    )
    assert detail.cleaned_content == (
        "用户最初提交的历史正文。"
    )
    assert detail.created_by == database_user.id
    assert detail.superseded_by == database_user.id

    # 当前第 2 版不在历史表中，因此应该返回 None。
    current_version_result = (
        get_document_version_by_number(
            database_session,
            document.id,
            2,
        )
    )
    assert current_version_result is None


# 验证文档存在但没有历史版本时返回空列表。
def test_list_document_versions_returns_empty_page_for_new_document(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        document_id="new-document-without-history",
        created_by=database_user.id,
    )
    database_session.add(
        document
    )
    database_session.commit()

    page = list_document_versions(
        database_session,
        document.id,
    )

    assert page is not None
    assert page.current_version == 1
    assert page.total == 0
    assert page.items == ()


# 验证历史版本会按版本号倒序返回并支持分页。
def test_list_document_versions_returns_paginated_newest_first(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        document_id="document-with-history",
        created_by=database_user.id,
        title="当前第 4 版",
    )
    document.version = 4
    database_session.add(
        document
    )
    database_session.flush()

    database_session.add_all(
        [
            build_knowledge_document_version(
                version_id="history-version-1",
                document_id=document.id,
                version=1,
                title="历史第 1 版",
                created_by=database_user.id,
                superseded_by=database_user.id,
            ),
            build_knowledge_document_version(
                version_id="history-version-2",
                document_id=document.id,
                version=2,
                title="历史第 2 版",
                created_by=database_user.id,
                superseded_by=database_user.id,
            ),
            build_knowledge_document_version(
                version_id="history-version-3",
                document_id=document.id,
                version=3,
                title="历史第 3 版",
                created_by=database_user.id,
                superseded_by=database_user.id,
            ),
        ]
    )
    database_session.commit()

    # 全部顺序应为 3、2、1；跳过第一条后取两条。
    page = list_document_versions(
        database_session,
        document.id,
        limit=2,
        offset=1,
    )

    assert page is not None
    assert page.current_version == 4
    assert page.total == 3
    assert tuple(
        item.version
        for item in page.items
    ) == (
        2,
        1,
    )
    assert tuple(
        item.title
        for item in page.items
    ) == (
        "历史第 2 版",
        "历史第 1 版",
    )
    assert all(
        item.document_id == document.id
        for item in page.items
    )
    assert all(
        item.embedding_dimensions
        == EMBEDDING_DIMENSIONS
        for item in page.items
    )

    persisted_version_count = database_session.scalar(
        select(
            func.count(
                KnowledgeDocumentVersionRecord.id
            )
        )
    )
    assert persisted_version_count == 3


# 这个函数负责创建只有前两个数值不同的 1024 维向量。
def build_pgvector_test_vector(
    first_value: float,
    second_value: float,
) -> list[float]:
    return [
        first_value,
        second_value,
        *(
            [0.0]
            * (EMBEDDING_DIMENSIONS - 2)
        ),
    ]

@pytest.mark.integration
@pytest.mark.postgres
# 这个测试负责验证真实 pgvector 会按照余弦相似度排序。
def test_search_ready_chunks_orders_real_pgvector_results(
    postgres_session: Session,
) -> None:
    # 在真实 PostgreSQL 测试库中创建一个用户。
    user = build_user()
    postgres_session.add(user)
    postgres_session.flush()

    # 创建一篇可以参与知识检索的文档。
    document = build_knowledge_document(
        created_by=user.id,
        title="密码重置知识",
        raw_content=(
            "用户可以通过邮箱或者人工客服重置密码。"
        ),
        embedding_model="fake-embedding-model",
    )

    # Repository 只允许检索 ready 状态的文档。
    document.status = (
        KnowledgeDocumentStatus.READY.value
    )

    # 与查询向量完全相同，相似度应该是 1.0。
    exact_chunk = build_knowledge_chunk(
        chunk_index=0,
        content="用户可以通过注册邮箱重置密码。",
        embedding=build_pgvector_test_vector(
            1.0,
            0.0,
        ),
    )
    exact_chunk.page_numbers = [2]

    # 与查询向量比较相似，相似度应该约为 0.8。
    related_chunk = build_knowledge_chunk(
        chunk_index=1,
        content="用户也可以联系人工客服修改密码。",
        embedding=build_pgvector_test_vector(
            0.8,
            0.6,
        ),
    )
    related_chunk.page_numbers = [3, 4]

    # 与查询向量不相似，相似度应该是 0。
    unrelated_chunk = build_knowledge_chunk(
        chunk_index=2,
        content="这是一个完全无关的知识片段。",
        embedding=build_pgvector_test_vector(
            0.0,
            1.0,
        ),
    )

    # 把三个知识片段关联到同一篇文档。
    document.chunks.extend(
        [
            exact_chunk,
            related_chunk,
            unrelated_chunk,
        ]
    )

    # 写入真实 PostgreSQL 测试数据库。
    add_document(
        postgres_session,
        document,
    )
    postgres_session.commit()

    # 使用与 exact_chunk 相同的查询向量。
    search_results = (
        search_ready_chunks_by_cosine(
            postgres_session,
            query_embedding=(
                build_pgvector_test_vector(
                    1.0,
                    0.0,
                )
            ),
            embedding_model=(
                "fake-embedding-model"
            ),
            embedding_dimensions=(
                EMBEDDING_DIMENSIONS
            ),

            # 最多只返回两个结果。
            top_k=2,

            # 排除相似度为 0 的无关片段。
            min_similarity=0.1,
        )
    )

    # 应该只返回完全相似和比较相似的两个片段。
    assert len(search_results) == 2

    # 完全相同的向量应该排在第一位。
    assert (
        search_results[0].chunk_id
        == exact_chunk.id
    )

    # 相似度约为 0.8 的片段应该排在第二位。
    assert (
        search_results[1].chunk_id
        == related_chunk.id
    )

    # PostgreSQL JSON 列读出列表，Repository 返回不可变页码元组。
    assert search_results[0].page_numbers == (2,)
    assert search_results[1].page_numbers == (3, 4)

    # 完全相同向量的余弦距离应该约为 0。
    assert (
        search_results[0].cosine_distance
        == pytest.approx(
            0.0,
            abs=0.000001,
        )
    )

    # related 向量的相似度约为 0.8，
    # 所以余弦距离约为 0.2。
    assert (
        search_results[1].cosine_distance
        == pytest.approx(
            0.2,
            abs=0.000001,
        )
    )
