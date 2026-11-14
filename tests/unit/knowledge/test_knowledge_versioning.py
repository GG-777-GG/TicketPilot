"""测试历史版本恢复 Service 的业务编排。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

import ticketpilot.knowledge.application.versioning as versioning
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
)
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeRepositoryError,
)
from ticketpilot.knowledge.application.updating import (
    InvalidKnowledgeDocumentUpdateStateError,
    KnowledgeDocumentNoChangesError,
    KnowledgeDocumentUpdateError,
    KnowledgeDocumentVersionConflictError,
)
from ticketpilot.knowledge.application.versioning import (
    KnowledgeDocumentVersionRestoreError,
    restore_document_version,
)


# 验证 Service 会把历史原始内容交给 Update Service。
def test_restore_document_version_reuses_update_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    provider = MagicMock(
        spec=EmbeddingProvider
    )
    restored_document = MagicMock(
        spec=KnowledgeDocumentRecord
    )
    historical_version = SimpleNamespace(
        title="历史第 1 版",
        raw_content="  历史原始正文。  ",
    )
    received_arguments: dict[str, object] = {}

    monkeypatch.setattr(
        versioning,
        "get_document_version_by_number",
        lambda *args, **kwargs: historical_version,
    )

    def fake_update_document(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        received_arguments.update(
            kwargs
        )
        return restored_document

    monkeypatch.setattr(
        versioning,
        "update_document",
        fake_update_document,
    )

    result = restore_document_version(
        session=session,
        document_id="document-001",
        version=1,
        expected_version=3,
        restored_by="admin-001",
        embedding_provider=provider,
    )

    assert result is restored_document
    session.rollback.assert_called_once_with()
    assert received_arguments["session"] is session
    assert received_arguments["document_id"] == (
        "document-001"
    )
    assert received_arguments["expected_version"] == 3
    assert received_arguments["updated_by"] == "admin-001"
    assert received_arguments["embedding_provider"] is provider

    raw_document = received_arguments[
        "raw_document"
    ]
    assert raw_document.title == "历史第 1 版"
    assert raw_document.content == (
        "  历史原始正文。  "
    )


# 验证历史版本不存在时返回 None，并且不进入更新流程。
def test_restore_document_version_returns_none_when_history_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    update_mock = MagicMock()
    monkeypatch.setattr(
        versioning,
        "get_document_version_by_number",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        versioning,
        "update_document",
        update_mock,
    )

    result = restore_document_version(
        session=session,
        document_id="document-001",
        version=99,
        expected_version=3,
        restored_by="admin-001",
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    assert result is None
    session.rollback.assert_called_once_with()
    update_mock.assert_not_called()


# 验证历史版本查询错误会转换成恢复业务统一异常。
def test_restore_document_version_wraps_repository_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )

    def failing_history_query(
        *args,
        **kwargs,
    ) -> None:
        raise KnowledgeRepositoryError(
            "模拟历史版本查询失败"
        )

    monkeypatch.setattr(
        versioning,
        "get_document_version_by_number",
        failing_history_query,
    )

    with pytest.raises(
        KnowledgeDocumentVersionRestoreError,
        match="读取待恢复的知识文档历史版本失败",
    ) as exception_info:
        restore_document_version(
            session=session,
            document_id="document-001",
            version=1,
            expected_version=3,
            restored_by="admin-001",
            embedding_provider=MagicMock(
                spec=EmbeddingProvider
            ),
        )

    assert isinstance(
        exception_info.value.__cause__,
        KnowledgeRepositoryError,
    )
    session.rollback.assert_called_once_with()


# 验证 Update Service 内部错误会转换成恢复业务统一异常。
def test_restore_document_version_wraps_update_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    monkeypatch.setattr(
        versioning,
        "get_document_version_by_number",
        lambda *args, **kwargs: SimpleNamespace(
            title="历史标题",
            raw_content="历史正文",
        ),
    )

    def failing_update_document(
        **kwargs,
    ) -> None:
        raise KnowledgeDocumentUpdateError(
            "模拟更新失败"
        )

    monkeypatch.setattr(
        versioning,
        "update_document",
        failing_update_document,
    )

    with pytest.raises(
        KnowledgeDocumentVersionRestoreError,
        match="恢复知识文档历史版本失败",
    ) as exception_info:
        restore_document_version(
            session=session,
            document_id="document-001",
            version=1,
            expected_version=3,
            restored_by="admin-001",
            embedding_provider=MagicMock(
                spec=EmbeddingProvider
            ),
        )

    assert isinstance(
        exception_info.value.__cause__,
        KnowledgeDocumentUpdateError,
    )


@pytest.mark.parametrize(
    "business_error",
    [
        pytest.param(
            KnowledgeDocumentVersionConflictError(
                "文档版本已经发生变化"
            ),
            id="version-conflict",
        ),
        pytest.param(
            InvalidKnowledgeDocumentUpdateStateError(
                "当前状态不允许更新"
            ),
            id="invalid-state",
        ),
        pytest.param(
            KnowledgeDocumentNoChangesError(
                "知识文档内容没有变化"
            ),
            id="no-changes",
        ),
    ],
)
# 验证可预期业务冲突不会被包装成内部错误。
def test_restore_document_version_preserves_business_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    business_error: ValueError,
) -> None:
    session = MagicMock(
        spec=Session
    )
    monkeypatch.setattr(
        versioning,
        "get_document_version_by_number",
        lambda *args, **kwargs: SimpleNamespace(
            title="历史标题",
            raw_content="历史正文",
        ),
    )

    def conflicting_update_document(
        **kwargs,
    ) -> None:
        raise business_error

    monkeypatch.setattr(
        versioning,
        "update_document",
        conflicting_update_document,
    )

    with pytest.raises(
        type(business_error),
        match=str(business_error),
    ):
        restore_document_version(
            session=session,
            document_id="document-001",
            version=1,
            expected_version=3,
            restored_by="admin-001",
            embedding_provider=MagicMock(
                spec=EmbeddingProvider
            ),
        )
