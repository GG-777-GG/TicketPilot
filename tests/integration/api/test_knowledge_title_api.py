"""验证知识文档标题专用接口的权限、校验和数据完整性。"""

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from tests.factories.knowledge import build_knowledge_chunk, build_knowledge_document
from ticketpilot.app import app
from ticketpilot.core.database import get_session
from ticketpilot.identity.dependencies import get_current_user
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.api.dependencies import get_embedding_provider
from ticketpilot.knowledge.domain.documents import KnowledgeDocumentStatus
from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentVersionRecord,
)
from ticketpilot.knowledge.application.updating import KnowledgeDocumentVersionConflictError
import ticketpilot.knowledge.api.router as knowledge_api


client = TestClient(app)
TITLE_URL = "/v1/knowledge/documents/{document_id}/title"


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Generator[None, None, None]:
    """每项测试前后清除 FastAPI 依赖覆盖。"""

    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def configure_dependencies(session: Session, user: UserRecord) -> None:
    """让接口使用指定的测试 Session 和用户。"""

    def use_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = lambda: user


def test_title_patch_changes_only_title_and_preserves_document_data(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    """真实 HTTP 请求只改标题和版本，不碰正文、片段或向量。"""

    document = build_knowledge_document(
        created_by=database_user.id,
        document_id="title-patch-document",
        title="旧标题",
        raw_content="维修部分保修叁个月。",
    )
    document.status = KnowledgeDocumentStatus.READY.value
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="title-patch-chunk",
            content="维修部分保修叁个月。",
            embedding=[0.25] * EMBEDDING_DIMENSIONS,
        )
    )
    database_session.add(document)
    database_session.commit()
    configure_dependencies(database_session, database_user)

    def fail_if_embedding_provider_is_requested() -> None:
        raise AssertionError("只改标题不应取得向量模型")

    app.dependency_overrides[get_embedding_provider] = (
        fail_if_embedding_provider_is_requested
    )

    response = client.patch(
        TITLE_URL.format(document_id=document.id),
        json={"title": "  维修合同  ", "expected_version": 1},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "维修合同"
    assert response.json()["version"] == 2
    assert response.json()["chunk_count"] == 1

    database_session.expire_all()
    saved_document = database_session.get(
        KnowledgeDocumentRecord, "title-patch-document"
    )
    saved_chunks = database_session.scalars(
        select(KnowledgeChunkRecord).where(
            KnowledgeChunkRecord.document_id == "title-patch-document"
        )
    ).all()
    saved_history = database_session.scalar(
        select(KnowledgeDocumentVersionRecord).where(
            KnowledgeDocumentVersionRecord.document_id == "title-patch-document"
        )
    )

    assert saved_document is not None
    assert saved_document.raw_content == "维修部分保修叁个月。"
    assert saved_document.cleaned_content == "维修部分保修叁个月。"
    assert len(saved_chunks) == 1
    assert saved_chunks[0].id == "title-patch-chunk"
    assert saved_chunks[0].embedding == [0.25] * EMBEDDING_DIMENSIONS
    assert saved_history is not None
    assert saved_history.title == "旧标题"


def test_title_patch_rejects_content_field_before_service(
    database_session: Session,
    database_user: UserRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """误传正文返回 422，业务函数不会运行。"""

    configure_dependencies(database_session, database_user)
    service = MagicMock()
    monkeypatch.setattr(knowledge_api, "update_document_title", service)

    response = client.patch(
        TITLE_URL.format(document_id="test-document"),
        json={
            "title": "维修合同",
            "expected_version": 3,
            "content": "错误提交的正文",
        },
    )

    assert response.status_code == 422
    service.assert_not_called()


def test_title_patch_requires_login() -> None:
    """没有 JWT 时不能修改标题。"""

    response = client.patch(
        TITLE_URL.format(document_id="test-document"),
        json={"title": "维修合同", "expected_version": 1},
    )

    assert response.status_code == 401


def test_title_patch_forbids_agent_role(
    database_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通 agent 用户没有文档管理权限。"""

    configure_dependencies(database_session, build_user(role="agent"))
    service = MagicMock()
    monkeypatch.setattr(knowledge_api, "update_document_title", service)

    response = client.patch(
        TITLE_URL.format(document_id="test-document"),
        json={"title": "维修合同", "expected_version": 1},
    )

    assert response.status_code == 403
    service.assert_not_called()


def test_title_patch_returns_409_for_version_conflict(
    database_session: Session,
    database_user: UserRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """过期版本不能覆盖当前标题。"""

    configure_dependencies(database_session, database_user)
    service = MagicMock(
        side_effect=KnowledgeDocumentVersionConflictError("当前版本为 4")
    )
    monkeypatch.setattr(knowledge_api, "update_document_title", service)

    response = client.patch(
        TITLE_URL.format(document_id="test-document"),
        json={"title": "维修合同", "expected_version": 3},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "当前版本为 4"


def test_title_patch_returns_404_when_document_is_missing(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    """找不到文档时返回 404。"""

    configure_dependencies(database_session, database_user)

    response = client.patch(
        TITLE_URL.format(document_id="missing-document"),
        json={"title": "维修合同", "expected_version": 1},
    )

    assert response.status_code == 404
