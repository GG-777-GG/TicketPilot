"""测试知识文档版本更新 Service。"""

from datetime import (
    UTC,
    datetime,
)
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from tests.factories.knowledge import (
    build_knowledge_document,
)
import ticketpilot.knowledge.application.updating as knowledge_updating
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
    RawDocument,
)
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
    EmbeddingProviderError,
)


TEST_TIME = datetime(
    2026,
    1,
    1,
    tzinfo=UTC,
)


def build_ready_document():
    """构造可以执行版本更新的当前文档。"""

    document = build_knowledge_document(
        created_by="creator-001",
        document_id="document-001",
        title="旧版密码说明",
        raw_content="员工使用旧流程重置密码。",
    )
    document.status = (
        KnowledgeDocumentStatus.READY.value
    )
    document.version = 1
    document.created_at = TEST_TIME
    document.updated_at = TEST_TIME
    return document


def build_embedding_provider() -> MagicMock:
    """构造返回一个有效向量的模拟 Provider。"""

    provider = MagicMock(
        spec=EmbeddingProvider
    )
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-update-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=(
            tuple(
                [0.25] * EMBEDDING_DIMENSIONS
            ),
        ),
    )
    return provider


# 验证成功更新会保存历史快照并生成下一版本。
def test_update_document_saves_history_and_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_ready_document()
    provider = build_embedding_provider()
    saved_histories = []
    saved_chunks = []

    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )
    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_for_update",
        lambda current_session, document_id: document,
    )

    def fake_add_document_version(
        current_session,
        history,
    ):
        saved_histories.append(history)
        return history

    def fake_replace_document_chunks(
        current_session,
        current_document,
        new_chunks,
    ):
        saved_chunks.extend(new_chunks)
        return current_document

    monkeypatch.setattr(
        knowledge_updating,
        "add_document_version",
        fake_add_document_version,
    )
    monkeypatch.setattr(
        knowledge_updating,
        "replace_document_chunks",
        fake_replace_document_chunks,
    )

    updated_document = knowledge_updating.update_document(
        session=session,
        document_id="document-001",
        raw_document=RawDocument(
            title="新版密码说明",
            content="员工使用企业邮箱验证后重置密码。",
        ),
        expected_version=1,
        updated_by="updater-001",
        embedding_provider=provider,
    )

    assert updated_document is document
    assert document.title == "新版密码说明"
    assert document.version == 2
    assert document.updated_by == "updater-001"
    assert document.embedding_model == "fake-update-model"
    assert len(saved_histories) == 1
    assert saved_histories[0].version == 1
    assert saved_histories[0].title == "旧版密码说明"
    assert saved_histories[0].superseded_by == "updater-001"
    assert len(saved_chunks) == 1
    assert saved_chunks[0].content == (
        "员工使用企业邮箱验证后重置密码。"
    )
    assert session.rollback.call_count == 1
    session.refresh.assert_called_once_with(document)
    session.commit.assert_called_once_with()


# 验证不存在的文档不会调用向量模型。
def test_update_document_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    provider = build_embedding_provider()
    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_by_id",
        lambda current_session, document_id: None,
    )

    result = knowledge_updating.update_document(
        session=session,
        document_id="missing-document",
        raw_document=RawDocument(
            title="新标题",
            content="有效的新正文。",
        ),
        expected_version=1,
        updated_by="updater-001",
        embedding_provider=provider,
    )

    assert result is None
    provider.embed_texts.assert_not_called()
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


# 验证过期版本号会在调用向量模型前被拒绝。
def test_update_document_rejects_stale_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_ready_document()
    document.version = 2
    provider = build_embedding_provider()
    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )

    with pytest.raises(
        knowledge_updating.KnowledgeDocumentVersionConflictError,
        match="当前版本为 2",
    ):
        knowledge_updating.update_document(
            session=session,
            document_id="document-001",
            raw_document=RawDocument(
                title="新标题",
                content="有效的新正文。",
            ),
            expected_version=1,
            updated_by="updater-001",
            embedding_provider=provider,
        )

    provider.embed_texts.assert_not_called()
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


# 验证非 ready 状态的文档不能更新。
def test_update_document_rejects_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_ready_document()
    document.status = (
        KnowledgeDocumentStatus.ARCHIVED.value
    )
    provider = build_embedding_provider()
    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )

    with pytest.raises(
        knowledge_updating.InvalidKnowledgeDocumentUpdateStateError,
    ):
        knowledge_updating.update_document(
            session=session,
            document_id="document-001",
            raw_document=RawDocument(
                title="新标题",
                content="有效的新正文。",
            ),
            expected_version=1,
            updated_by="updater-001",
            embedding_provider=provider,
        )

    provider.embed_texts.assert_not_called()
    session.rollback.assert_called_once_with()


# 验证标题和正文都没变化时不会创建空版本。
def test_update_document_rejects_unchanged_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_ready_document()
    provider = build_embedding_provider()
    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )

    with pytest.raises(
        knowledge_updating.KnowledgeDocumentNoChangesError,
    ):
        knowledge_updating.update_document(
            session=session,
            document_id="document-001",
            raw_document=RawDocument(
                title=document.title,
                content=document.raw_content,
            ),
            expected_version=1,
            updated_by="updater-001",
            embedding_provider=provider,
        )

    provider.embed_texts.assert_not_called()
    session.rollback.assert_called_once_with()


# 验证向量服务失败会转换成统一更新异常并回滚。
def test_update_document_rolls_back_when_embedding_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(
        spec=Session
    )
    document = build_ready_document()
    provider = build_embedding_provider()
    provider.embed_texts.side_effect = EmbeddingProviderError(
        "模拟向量服务失败"
    )
    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_by_id",
        lambda current_session, document_id: document,
    )

    with pytest.raises(
        knowledge_updating.KnowledgeDocumentUpdateError,
        match="知识文档更新失败",
    ) as exception_info:
        knowledge_updating.update_document(
            session=session,
            document_id="document-001",
            raw_document=RawDocument(
                title="新标题",
                content="有效的新正文。",
            ),
            expected_version=1,
            updated_by="updater-001",
            embedding_provider=provider,
        )

    assert isinstance(
        exception_info.value.__cause__,
        EmbeddingProviderError,
    )
    assert session.rollback.call_count == 2
    session.commit.assert_not_called()


# 验证只改标题时找不到文档会返回 None，不创建版本。
def test_update_document_title_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_for_update",
        lambda current_session, document_id: None,
    )

    result = knowledge_updating.update_document_title(
        session=session,
        document_id="missing-document",
        title="新标题",
        expected_version=1,
        updated_by="updater-001",
    )

    assert result is None
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    ("status", "version", "new_title", "error_type"),
    [
        (
            KnowledgeDocumentStatus.READY.value,
            2,
            "新标题",
            knowledge_updating.KnowledgeDocumentVersionConflictError,
        ),
        (
            KnowledgeDocumentStatus.ARCHIVED.value,
            1,
            "新标题",
            knowledge_updating.InvalidKnowledgeDocumentUpdateStateError,
        ),
        (
            KnowledgeDocumentStatus.READY.value,
            1,
            "旧版密码说明",
            knowledge_updating.KnowledgeDocumentNoChangesError,
        ),
    ],
)
def test_update_document_title_rejects_conflicts_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    version: int,
    new_title: str,
    error_type: type[ValueError],
) -> None:
    session = MagicMock(spec=Session)
    document = build_ready_document()
    document.status = status
    document.version = version
    add_history = MagicMock()
    monkeypatch.setattr(
        knowledge_updating,
        "get_document_record_for_update",
        lambda current_session, document_id: document,
    )
    monkeypatch.setattr(
        knowledge_updating,
        "add_document_version",
        add_history,
    )

    with pytest.raises(error_type):
        knowledge_updating.update_document_title(
            session=session,
            document_id=document.id,
            title=new_title,
            expected_version=1,
            updated_by="updater-001",
        )

    add_history.assert_not_called()
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
