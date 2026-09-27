"""
测试知识库 SQLAlchemy ORM 模型。

本阶段测试：
- 两张知识库表已经登记到 Base.metadata。
- SQLite 可以使用 JSON 代替 Vector 字段。
- 文档和片段能够正常保存。
- ORM 关系能够从文档访问片段。
- 片段能够反向访问所属文档。
- 片段按照 chunk_index 排序。
- Python 和数据库默认值能够生效。
- 删除文档时能够级联删除所属片段。

下一阶段会继续增加：
- 唯一约束测试
- CheckConstraint 测试
- 外键约束测试
- PostgreSQL 真实 Vector(1024) 集成测试
"""

# uuid4 用来生成测试记录的唯一 ID。
from uuid import uuid4

# pytest 提供参数化和异常断言功能。
import pytest

# select 用于执行 SQLAlchemy 2.x 查询。
from sqlalchemy import select

# IntegrityError 表示数据库唯一约束、检查约束或外键约束失败。
from sqlalchemy.exc import IntegrityError

# Session 表示一次数据库会话。
from sqlalchemy.orm import Session

# 测试数据工厂只构造 ORM 对象，不执行数据库操作。
from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
)

# 导入项目统一的 ORM Base。
from ticketpilot.core.database import Base

# UserRecord 用于标注公共 database_user fixture 的类型。
from ticketpilot.identity.models import UserRecord

# 导入知识向量维度。
from ticketpilot.knowledge.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入知识库 ORM 模型。
from ticketpilot.knowledge.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)


# 验证知识库表已经登记到 Base.metadata。
def test_knowledge_tables_are_registered() -> None:
    # knowledge_documents 表必须存在于 metadata。
    assert "knowledge_documents" in (
        Base.metadata.tables
    )

    # knowledge_chunks 表也必须存在。
    assert "knowledge_chunks" in (
        Base.metadata.tables
    )


# 验证文档、片段、默认值、关系和级联删除。
def test_document_and_chunks_can_be_saved(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    # factory 只构造文档；用户由公共 fixture 提供。
    document = build_knowledge_document(
        created_by=database_user.id
    )

    # 故意先添加 chunk_index=1，
    # 再添加 chunk_index=0。
    # 保存后 relationship 应按 index 排序。
    document.chunks.extend(
        [
            build_knowledge_chunk(
                chunk_index=1,
                content="重置密码。",
                start_offset=7,
                end_offset=12,
            ),
            build_knowledge_chunk(
                chunk_index=0,
                content="用户可以通过邮箱",
                start_offset=0,
                end_offset=8,
            ),
        ]
    )

    # 把 Document 加入 Session。
    # cascade 会同时保存它的两个 Chunk。
    database_session.add(
        document
    )

    # 提交事务。
    database_session.commit()

    # 保存 ID，后面重新读取。
    document_id = document.id

    # 清除 Session 中已加载的字段和关系缓存。
    database_session.expire_all()

    # 根据主键重新取得文档。
    saved_document = database_session.get(
        KnowledgeDocumentRecord,
        document_id,
    )

    # 数据库必须能查到这篇文档。
    assert saved_document is not None

    # 没有显式传 source_type，
    # SQLAlchemy 应使用默认 manual。
    assert (
        saved_document.source_type
        == "manual"
    )

    # 没有显式传 status，
    # 默认状态应该是 pending。
    assert (
        saved_document.status
        == "pending"
    )

    # 默认向量维度应该是 1024。
    assert (
        saved_document.embedding_dimensions
        == EMBEDDING_DIMENSIONS
    )

    # 第一版文档版本默认为 1。
    assert saved_document.version == 1

    # 数据库应该自动生成时间。
    assert saved_document.created_at is not None
    assert saved_document.updated_at is not None

    # 文档应该拥有两个片段。
    assert len(
        saved_document.chunks
    ) == 2

    # relationship 中的片段应按照 chunk_index 排序。
    assert [
        chunk.chunk_index
        for chunk in saved_document.chunks
    ] == [
        0,
        1,
    ]

    # SQLite JSON 能够恢复成 Python list。
    assert isinstance(
        saved_document.chunks[0].embedding,
        list,
    )

    # 向量长度应该保持为 1024。
    assert len(
        saved_document.chunks[0].embedding
    ) == EMBEDDING_DIMENSIONS

    # Chunk 可以反向访问所属 Document。
    assert (
        saved_document.chunks[0].document
        is saved_document
    )

    # 删除文档 ORM 对象。
    database_session.delete(
        saved_document
    )

    # 提交删除事务。
    database_session.commit()

    # 查询全部知识片段。
    remaining_chunks = (
        database_session.scalars(
            select(
                KnowledgeChunkRecord
            )
        )
        .all()
    )

    # 删除文档后不能遗留孤立片段。
    assert remaining_chunks == []

# 使用多组错误字段验证文档表的 CheckConstraint。
@pytest.mark.parametrize(
    (
        "field_name",
        "invalid_value",
    ),
    [
        # 不支持的文档来源。
        (
            "source_type",
            "email",
        ),

        # 不存在的文档状态。
        (
            "status",
            "unknown",
        ),

        # 不存在的处理阶段。
        (
            "processing_stage",
            "unknown",
        ),

        # 必须与 Vector(1024) 保持一致。
        (
            "embedding_dimensions",
            768,
        ),

        # 文档版本必须大于 0。
        (
            "version",
            0,
        ),
    ],
)
def test_document_check_constraints_reject_invalid_values(
    database_session: Session,
    database_user: UserRecord,
    field_name: str,
    invalid_value: object,
) -> None:
    # 创建一篇基础字段都正确的文档。
    document = build_knowledge_document(
        created_by=database_user.id
    )

    # 根据参数修改其中一个字段。
    #
    # 例如：
    # setattr(document, "status", "unknown")
    # 等价于：
    # document.status = "unknown"
    setattr(
        document,
        field_name,
        invalid_value,
    )

    # 将包含错误字段的文档加入 Session。
    database_session.add(
        document
    )

    # commit() 会真正执行 INSERT，
    # SQLite 的 CheckConstraint 应拒绝错误数据。
    with pytest.raises(
        IntegrityError
    ):
        database_session.commit()

    # 数据库异常后，Session 会停留在失败事务中。
    # rollback() 将 Session 恢复成可继续使用的状态。
    database_session.rollback()


# 验证同一文档不能拥有重复的 chunk_index。
def test_duplicate_chunk_index_is_rejected(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    # 创建测试文档。
    document = build_knowledge_document(
        created_by=database_user.id
    )

    # 添加两个 chunk_index 都为 0 的片段。
    document.chunks.extend(
        [
            build_knowledge_chunk(
                chunk_index=0,
                content="第一个片段",
                start_offset=0,
                end_offset=5,
            ),
            build_knowledge_chunk(
                chunk_index=0,
                content="重复编号的片段",
                start_offset=5,
                end_offset=12,
            ),
        ]
    )

    # 添加文档及其片段。
    database_session.add(
        document
    )

    # document_id + chunk_index 唯一约束应拒绝提交。
    with pytest.raises(
        IntegrityError
    ):
        database_session.commit()

    # 恢复 Session。
    database_session.rollback()


# 使用多组参数验证片段范围约束。
@pytest.mark.parametrize(
    (
        "chunk_index",
        "start_offset",
        "end_offset",
    ),
    [
        # chunk_index 不能是负数。
        (
            -1,
            0,
            5,
        ),

        # start_offset 不能是负数。
        (
            0,
            -1,
            5,
        ),

        # end_offset 不能等于 start_offset。
        (
            0,
            5,
            5,
        ),

        # end_offset 不能位于 start_offset 前面。
        (
            0,
            8,
            5,
        ),
    ],
)
def test_chunk_check_constraints_reject_invalid_ranges(
    database_session: Session,
    database_user: UserRecord,
    chunk_index: int,
    start_offset: int,
    end_offset: int,
) -> None:
    # 创建测试文档。
    document = build_knowledge_document(
        created_by=database_user.id
    )

    # 创建具有错误编号或 offset 的片段。
    invalid_chunk = build_knowledge_chunk(
        chunk_index=chunk_index,
        content="无效片段",
        start_offset=start_offset,
        end_offset=end_offset,
    )

    # 把错误片段关联到文档。
    document.chunks.append(
        invalid_chunk
    )

    # 添加文档。
    database_session.add(
        document
    )

    # 数据库 CheckConstraint 应拒绝提交。
    with pytest.raises(
        IntegrityError
    ):
        database_session.commit()

    # 恢复失败后的 Session。
    database_session.rollback()


# 验证 created_by 必须指向真实存在的用户。
def test_document_creator_must_exist(
    database_session: Session,
) -> None:
    # 使用一个没有保存到 users 表中的随机用户 ID。
    missing_user_id = str(
        uuid4()
    )

    # 创建引用不存在用户的文档。
    document = build_knowledge_document(
        created_by=missing_user_id
    )

    # 添加文档。
    database_session.add(
        document
    )

    # SQLite fixture 已开启外键约束，
    # 所以提交时必须失败。
    with pytest.raises(
        IntegrityError
    ):
        database_session.commit()

    # 恢复 Session。
    database_session.rollback()
