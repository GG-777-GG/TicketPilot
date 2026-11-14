"""测试知识文档可见性字段的默认值和数据库约束。"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.factories.knowledge import build_knowledge_document
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.persistence.models import KnowledgeDocumentRecord


# 这个测试负责保证旧文档和新建文档默认都属于内部可见。
def test_document_visibility_defaults_to_internal(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    # 数据库默认值用于兼容未显式填写可见性的插入操作。
    visibility_column = KnowledgeDocumentRecord.__table__.c.visibility
    assert visibility_column.server_default is not None
    assert visibility_column.server_default.arg == "internal"

    document = build_knowledge_document(created_by=database_user.id)
    database_session.add(document)
    database_session.flush()

    # 从数据库重新读取，不能只检查 Python 对象上的临时属性。
    stored_visibility = database_session.scalar(
        select(KnowledgeDocumentRecord.visibility).where(
            KnowledgeDocumentRecord.id == document.id
        )
    )
    assert stored_visibility == "internal"


# 这个测试负责验证受限文档的可见性能够真实保存到数据库。
def test_restricted_document_visibility_is_persisted(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(created_by=database_user.id)
    document.visibility = "restricted"
    database_session.add(document)
    database_session.flush()

    stored_visibility = database_session.scalar(
        select(KnowledgeDocumentRecord.visibility).where(
            KnowledgeDocumentRecord.id == document.id
        )
    )
    assert stored_visibility == "restricted"


# 这个测试负责验证数据库拒绝未定义的可见性，防止权限规则被绕过。
def test_document_visibility_rejects_unknown_value(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    document = build_knowledge_document(created_by=database_user.id)
    document.visibility = "external"
    database_session.add(document)

    with pytest.raises(IntegrityError):
        database_session.flush()

    database_session.rollback()
