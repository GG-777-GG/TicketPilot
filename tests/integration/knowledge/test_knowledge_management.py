"""
使用真实临时数据库测试知识文档归档流程。

验证目标：
- 文档状态真正保存为 archived。
- 文档记录不会被物理删除。
- 文档所属知识片段不会被删除。
"""

# select 和 func 用来统计数据库记录数量。
from sqlalchemy import (
    func,
    select,
)

# Session 是集成测试数据库会话的类型。
from sqlalchemy.orm import Session

# 测试工厂负责构造有效的文档与片段 ORM 对象。
from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
)

# 测试用户用来满足知识文档的外键约束。
from ticketpilot.identity.models import UserRecord

# 导入真正的归档 Service。
from ticketpilot.knowledge.application.management import (
    archive_document,
    restore_document,
)

# 导入知识文档状态枚举。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
)

# 导入需要检查的文档和片段 ORM 模型。
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
)


# 这个测试负责验证归档只修改状态，不删除文档和片段。
def test_archive_document_preserves_document_and_chunks(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="document-to-archive",
        title="即将归档的知识文档",
        raw_content="这篇文档归档后仍然需要保留。",
    )
    document.status = (
        KnowledgeDocumentStatus.READY.value
    )
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="archived-document-chunk",
            content="归档时不能删除这个知识片段。",
        )
    )
    database_session.add(document)
    database_session.flush()

    archived = archive_document(
        session=database_session,
        document_id="document-to-archive",
    )

    # 清除 Session 缓存，确保下面读取的是数据库已提交状态。
    database_session.expire_all()

    saved_document = database_session.get(
        KnowledgeDocumentRecord,
        "document-to-archive",
    )
    chunk_count = database_session.scalar(
        select(
            func.count(KnowledgeChunkRecord.id)
        ).where(
            KnowledgeChunkRecord.document_id
            == "document-to-archive"
        )
    )

    assert archived is True
    assert saved_document is not None
    assert saved_document.status == (
        KnowledgeDocumentStatus.ARCHIVED.value
    )
    assert chunk_count == 1


# 这个测试负责验证恢复会重新启用文档并保留原有片段。
def test_restore_document_reactivates_archived_document(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="document-to-restore",
        title="准备恢复的知识文档",
        raw_content="恢复后这篇文档可以重新参与检索。",
    )
    document.status = (
        KnowledgeDocumentStatus.ARCHIVED.value
    )
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="restored-document-chunk",
            content="恢复时必须保留这个知识片段。",
        )
    )
    database_session.add(document)
    database_session.flush()

    restored = restore_document(
        session=database_session,
        document_id="document-to-restore",
    )

    database_session.expire_all()

    saved_document = database_session.get(
        KnowledgeDocumentRecord,
        "document-to-restore",
    )
    chunk_count = database_session.scalar(
        select(
            func.count(KnowledgeChunkRecord.id)
        ).where(
            KnowledgeChunkRecord.document_id
            == "document-to-restore"
        )
    )

    assert restored is True
    assert saved_document is not None
    assert saved_document.status == (
        KnowledgeDocumentStatus.READY.value
    )
    assert chunk_count == 1
