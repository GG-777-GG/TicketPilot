"""
测试知识文档管理 Service。

成功流程：
查询 ORM 文档
→ 修改状态为 archived
→ 清空处理阶段
→ commit

失败流程：
查询或提交发生异常
→ rollback
→ KnowledgeManagementError
"""

# MagicMock 用来模拟数据库 Session。
from unittest.mock import MagicMock

# pytest 用来替换依赖和验证预期异常。
import pytest

# SQLAlchemyError 用来模拟数据库提交失败。
from sqlalchemy.exc import SQLAlchemyError

# Session 用作模拟对象的接口规范。
from sqlalchemy.orm import Session

# 工厂负责构造有效的知识文档 ORM 对象。
from tests.factories.knowledge import (
    build_knowledge_document,
)

# 导入整个模块，方便 monkeypatch 替换其 Repository 函数。
import ticketpilot.knowledge.application.management as knowledge_management

# 导入知识文档状态枚举。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
)

# 导入 Repository 统一异常。
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeRepositoryError,
)


# 这个测试负责验证存在的文档会被归档并提交事务。
def test_archive_document_updates_status_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_knowledge_document(
        created_by="user-001",
    )
    # 工厂默认不设置处理阶段，这里模拟正在生成向量的文档。
    document.processing_stage = "embedding"

    monkeypatch.setattr(
        knowledge_management,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )

    archived = knowledge_management.archive_document(
        session=session,
        document_id="document-001",
    )

    assert archived is True
    assert document.status == (
        KnowledgeDocumentStatus.ARCHIVED.value
    )
    assert document.processing_stage is None
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


# 这个测试负责验证文档不存在时不会提交事务。
def test_archive_document_returns_false_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    monkeypatch.setattr(
        knowledge_management,
        "get_document_record_by_id",
        lambda current_session, document_id: None,
    )

    archived = knowledge_management.archive_document(
        session=session,
        document_id="missing-document",
    )

    assert archived is False
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "failure_stage",
    [
        "repository",
        "commit",
    ],
)
# 这个测试负责验证查询或提交失败时都会回滚事务。
def test_archive_document_rolls_back_when_operation_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_knowledge_document(
        created_by="user-001",
    )

    if failure_stage == "repository":
        def failing_get_document(
            current_session: Session,
            document_id: str,
        ) -> None:
            raise KnowledgeRepositoryError(
                "模拟 Repository 查询失败"
            )

        monkeypatch.setattr(
            knowledge_management,
            "get_document_record_by_id",
            failing_get_document,
        )
    else:
        monkeypatch.setattr(
            knowledge_management,
            "get_document_record_by_id",
            lambda current_session, document_id: document,
        )
        session.commit.side_effect = SQLAlchemyError(
            "模拟数据库提交失败"
        )

    with pytest.raises(
        knowledge_management.KnowledgeManagementError,
        match="知识文档归档失败",
    ):
        knowledge_management.archive_document(
            session=session,
            document_id="document-001",
        )

    session.rollback.assert_called_once_with()

    if failure_stage == "repository":
        session.commit.assert_not_called()
    else:
        session.commit.assert_called_once_with()


# 这个测试负责验证已归档文档会恢复成 ready 并提交事务。
def test_restore_document_updates_status_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_knowledge_document(
        created_by="user-001",
    )
    document.status = (
        KnowledgeDocumentStatus.ARCHIVED.value
    )
    document.processing_stage = "indexing"

    monkeypatch.setattr(
        knowledge_management,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )

    restored = knowledge_management.restore_document(
        session=session,
        document_id="document-001",
    )

    assert restored is True
    assert document.status == (
        KnowledgeDocumentStatus.READY.value
    )
    assert document.processing_stage is None
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


# 这个测试负责验证重复恢复 ready 文档是幂等操作。
def test_restore_document_is_idempotent_when_already_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_knowledge_document(
        created_by="user-001",
    )
    document.status = (
        KnowledgeDocumentStatus.READY.value
    )

    monkeypatch.setattr(
        knowledge_management,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )

    restored = knowledge_management.restore_document(
        session=session,
        document_id="document-001",
    )

    assert restored is True
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责验证恢复不存在的文档会返回 False。
def test_restore_document_returns_false_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    monkeypatch.setattr(
        knowledge_management,
        "get_document_record_by_id",
        lambda current_session, document_id: None,
    )

    restored = knowledge_management.restore_document(
        session=session,
        document_id="missing-document",
    )

    assert restored is False
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize(
    "document_status",
    [
        KnowledgeDocumentStatus.PENDING.value,
        KnowledgeDocumentStatus.PROCESSING.value,
        KnowledgeDocumentStatus.FAILED.value,
    ],
)
# 这个测试负责验证不允许恢复的状态会产生业务冲突。
def test_restore_document_rejects_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
    document_status: str,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_knowledge_document(
        created_by="user-001",
    )
    document.status = document_status

    monkeypatch.setattr(
        knowledge_management,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )

    with pytest.raises(
        knowledge_management.InvalidKnowledgeDocumentStateError,
        match="只有已归档的知识文档可以恢复",
    ):
        knowledge_management.restore_document(
            session=session,
            document_id="document-001",
        )

    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()


@pytest.mark.parametrize(
    "failure_stage",
    [
        "repository",
        "commit",
    ],
)
# 这个测试负责验证恢复过程中发生数据库错误会回滚。
def test_restore_document_rolls_back_when_operation_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_knowledge_document(
        created_by="user-001",
    )
    document.status = (
        KnowledgeDocumentStatus.ARCHIVED.value
    )

    if failure_stage == "repository":
        def failing_get_document(
            current_session: Session,
            document_id: str,
        ) -> None:
            raise KnowledgeRepositoryError(
                "模拟 Repository 查询失败"
            )

        monkeypatch.setattr(
            knowledge_management,
            "get_document_record_by_id",
            failing_get_document,
        )
    else:
        monkeypatch.setattr(
            knowledge_management,
            "get_document_record_by_id",
            lambda current_session, document_id: document,
        )
        session.commit.side_effect = SQLAlchemyError(
            "模拟数据库提交失败"
        )

    with pytest.raises(
        knowledge_management.KnowledgeManagementError,
        match="知识文档恢复失败",
    ):
        knowledge_management.restore_document(
            session=session,
            document_id="document-001",
        )

    session.rollback.assert_called_once_with()
