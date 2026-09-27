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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
)
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)
from ticketpilot.knowledge.repository import (
    add_document,
    search_ready_chunks_by_cosine,
)



import pytest

from tests.factories.identity import (
    build_user,
)
from ticketpilot.knowledge.constants import (
    EMBEDDING_DIMENSIONS,
)
from ticketpilot.knowledge.documents import (
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

    # 与查询向量比较相似，相似度应该约为 0.8。
    related_chunk = build_knowledge_chunk(
        chunk_index=1,
        content="用户也可以联系人工客服修改密码。",
        embedding=build_pgvector_test_vector(
            0.8,
            0.6,
        ),
    )

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



