"""测试知识文档历史版本恢复 API。"""

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
    build_knowledge_document_version,
)
from ticketpilot.app import app
from ticketpilot.core.database import get_session
from ticketpilot.identity.dependencies import (
    get_current_user,
)
from ticketpilot.identity.models import UserRecord
from ticketpilot.identity.types import UserRole
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)
from ticketpilot.knowledge.api.dependencies import (
    get_embedding_provider,
)
from ticketpilot.knowledge.domain.documents import (
    KnowledgeDocumentStatus,
)
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
)
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
    KnowledgeDocumentVersionRecord,
)
import ticketpilot.knowledge.api.router as knowledge_api


client = TestClient(
    app
)

TEST_TIMESTAMP = datetime(
    2026,
    1,
    1,
    tzinfo=UTC,
)


# 每项测试前后清除 FastAPI 依赖覆盖。
@pytest.fixture(
    autouse=True,
)
def clear_dependency_overrides() -> Generator[
    None,
    None,
    None,
]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# 为不执行真实 SQL 的 HTTP 边界测试提供 Session。
def fake_get_session() -> Generator[
    Session,
    None,
    None,
]:
    with Session() as session:
        yield session


# 构造指定角色的测试用户。
def build_test_user(
    role: UserRole,
) -> UserRecord:
    return UserRecord(
        id=f"{role}-version-restore-user",
        email=f"{role}-restore@example.com",
        display_name="版本恢复测试用户",
        password_hash="not-used-in-test",
        role=role,
        is_active=True,
        created_at=TEST_TIMESTAMP,
    )


# 覆盖认证、Session 和向量模型依赖。
def configure_dependencies(
    *,
    current_user: UserRecord,
    embedding_provider: EmbeddingProvider,
) -> None:
    app.dependency_overrides[
        get_session
    ] = fake_get_session
    app.dependency_overrides[
        get_current_user
    ] = lambda: current_user
    app.dependency_overrides[
        get_embedding_provider
    ] = lambda: embedding_provider


@pytest.mark.parametrize(
    "allowed_role",
    [
        pytest.param("supervisor", id="supervisor"),
        pytest.param("admin", id="admin"),
    ],
)
# 验证主管和管理员能够恢复历史版本。
def test_restore_knowledge_document_version_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    allowed_role: UserRole,
) -> None:
    current_user = build_test_user(
        allowed_role
    )
    provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=provider,
    )
    received_arguments: dict[str, object] = {}

    def fake_restore_document_version(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        received_arguments.update(
            kwargs
        )
        document = build_knowledge_document(
            document_id="document-001",
            created_by="original-creator",
            title="恢复后的历史标题",
            raw_content="恢复后的历史正文。",
            embedding_model="restore-model",
        )
        document.source_type = "manual"
        document.source_uri = None
        document.status = "ready"
        document.version = 4
        document.embedding_dimensions = (
            EMBEDDING_DIMENSIONS
        )
        document.created_at = TEST_TIMESTAMP
        document.updated_at = TEST_TIMESTAMP
        document.chunks.append(
            build_knowledge_chunk(
                chunk_id="restored-chunk",
                content="恢复后的历史正文。",
            )
        )
        return document

    monkeypatch.setattr(
        knowledge_api,
        "restore_document_version",
        fake_restore_document_version,
    )

    response = client.post(
        (
            "/v1/knowledge/documents/"
            "document-001/versions/1/restore"
        ),
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 200
    assert response.json()["version"] == 4
    assert response.json()["title"] == (
        "恢复后的历史标题"
    )
    assert response.json()["chunk_count"] == 1
    assert received_arguments["document_id"] == (
        "document-001"
    )
    assert received_arguments["version"] == 1
    assert received_arguments["expected_version"] == 3
    assert received_arguments["restored_by"] == (
        current_user.id
    )
    assert received_arguments["embedding_provider"] is provider


# 验证找不到历史版本时返回 404。
def test_restore_knowledge_document_version_returns_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dependencies(
        current_user=build_test_user("admin"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )
    monkeypatch.setattr(
        knowledge_api,
        "restore_document_version",
        lambda **kwargs: None,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/versions/99/restore",
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "知识文档历史版本不存在",
    }


@pytest.mark.parametrize(
    (
        "error_type",
        "error_message",
    ),
    [
        pytest.param(
            knowledge_api.KnowledgeDocumentVersionConflictError,
            "文档版本已经发生变化",
            id="version-conflict",
        ),
        pytest.param(
            knowledge_api.InvalidKnowledgeDocumentUpdateStateError,
            "当前状态不允许恢复",
            id="invalid-state",
        ),
        pytest.param(
            knowledge_api.KnowledgeDocumentNoChangesError,
            "历史内容与当前内容相同",
            id="no-changes",
        ),
    ],
)
# 验证恢复业务冲突会返回 409 和具体原因。
def test_restore_knowledge_document_version_returns_409(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[ValueError],
    error_message: str,
) -> None:
    configure_dependencies(
        current_user=build_test_user("admin"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    def conflicting_restore(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        raise error_type(
            error_message
        )

    monkeypatch.setattr(
        knowledge_api,
        "restore_document_version",
        conflicting_restore,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/versions/1/restore",
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": error_message,
    }


@pytest.mark.parametrize(
    "invalid_body",
    [
        pytest.param(
            {},
            id="missing-expected-version",
        ),
        pytest.param(
            {
                "expected_version": 0,
            },
            id="invalid-expected-version",
        ),
        pytest.param(
            {
                "expected_version": 3,
                "restored_by": "forged-user",
            },
            id="forged-restored-by",
        ),
    ],
)
# 验证非法请求正文在进入 Service 前返回 422。
def test_restore_knowledge_document_version_rejects_invalid_body(
    monkeypatch: pytest.MonkeyPatch,
    invalid_body: dict[str, object],
) -> None:
    configure_dependencies(
        current_user=build_test_user("supervisor"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )
    restore_mock = MagicMock()
    monkeypatch.setattr(
        knowledge_api,
        "restore_document_version",
        restore_mock,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/versions/1/restore",
        json=invalid_body,
    )

    assert response.status_code == 422
    restore_mock.assert_not_called()


@pytest.mark.parametrize(
    "request_url",
    [
        pytest.param(
            "/v1/knowledge/documents/document-001/versions/0/restore",
            id="version-too-small",
        ),
        pytest.param(
            f"/v1/knowledge/documents/{'x' * 37}/versions/1/restore",
            id="document-id-too-long",
        ),
    ],
)
# 验证非法路径参数返回 422。
def test_restore_knowledge_document_version_rejects_invalid_path(
    monkeypatch: pytest.MonkeyPatch,
    request_url: str,
) -> None:
    configure_dependencies(
        current_user=build_test_user("supervisor"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )
    restore_mock = MagicMock()
    monkeypatch.setattr(
        knowledge_api,
        "restore_document_version",
        restore_mock,
    )

    response = client.post(
        request_url,
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 422
    restore_mock.assert_not_called()


# 验证未登录用户不能恢复历史版本。
def test_restore_knowledge_document_version_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.dependency_overrides[
        get_session
    ] = fake_get_session
    app.dependency_overrides[
        get_embedding_provider
    ] = lambda: MagicMock(
        spec=EmbeddingProvider
    )
    restore_mock = MagicMock()
    monkeypatch.setattr(
        knowledge_api,
        "restore_document_version",
        restore_mock,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/versions/1/restore",
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }
    restore_mock.assert_not_called()


# 验证普通客服不能恢复历史版本。
def test_restore_knowledge_document_version_forbids_agent_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dependencies(
        current_user=build_test_user("agent"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )
    restore_mock = MagicMock()
    monkeypatch.setattr(
        knowledge_api,
        "restore_document_version",
        restore_mock,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/versions/1/restore",
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "权限不足",
    }
    restore_mock.assert_not_called()


# 验证恢复内部失败时返回安全的 HTTP 500。
def test_restore_knowledge_document_version_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dependencies(
        current_user=build_test_user("admin"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    def failing_restore(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        raise knowledge_api.KnowledgeDocumentVersionRestoreError(
            "这里是不能泄漏给客户端的内部错误"
        )

    monkeypatch.setattr(
        knowledge_api,
        "restore_document_version",
        failing_restore,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/versions/1/restore",
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }
    assert "不能泄漏" not in response.text


# 验证 HTTP 请求会真实创建递增的新版本。
def test_restore_knowledge_document_version_full_http_flow(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    database_session.commit()
    user_id = database_user.id

    document = build_knowledge_document(
        document_id="http-restore-version-document",
        created_by=user_id,
        title="当前第 3 版",
        raw_content="当前第 3 版正文。",
    )
    document.version = 3
    document.status = (
        KnowledgeDocumentStatus.READY.value
    )
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="http-current-chunk",
            content="当前第 3 版正文。",
        )
    )
    database_session.add(
        document
    )
    database_session.flush()
    database_session.add_all(
        [
            build_knowledge_document_version(
                version_id="http-restore-history-1",
                document_id=document.id,
                version=1,
                title="历史第 1 版",
                raw_content="历史第 1 版正文。",
                created_by=user_id,
                superseded_by=user_id,
            ),
            build_knowledge_document_version(
                version_id="http-restore-history-2",
                document_id=document.id,
                version=2,
                title="历史第 2 版",
                raw_content="历史第 2 版正文。",
                created_by=user_id,
                superseded_by=user_id,
            ),
        ]
    )
    database_session.commit()

    provider = MagicMock(
        spec=EmbeddingProvider
    )
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="http-restore-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=(
            tuple(
                [0.8] * EMBEDDING_DIMENSIONS
            ),
        ),
    )

    def use_database_session() -> Generator[
        Session,
        None,
        None,
    ]:
        yield database_session

    app.dependency_overrides[
        get_session
    ] = use_database_session
    app.dependency_overrides[
        get_current_user
    ] = lambda: database_user
    app.dependency_overrides[
        get_embedding_provider
    ] = lambda: provider

    response = client.post(
        (
            "/v1/knowledge/documents/"
            "http-restore-version-document/versions/1/restore"
        ),
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 200
    assert response.json()["version"] == 4
    assert response.json()["title"] == "历史第 1 版"

    database_session.expire_all()
    saved_document = database_session.get(
        KnowledgeDocumentRecord,
        document.id,
    )
    saved_versions = database_session.scalars(
        select(
            KnowledgeDocumentVersionRecord
        )
        .where(
            KnowledgeDocumentVersionRecord.document_id
            == document.id
        )
        .order_by(
            KnowledgeDocumentVersionRecord.version.asc()
        )
    ).all()

    assert saved_document is not None
    assert saved_document.version == 4
    assert saved_document.raw_content == "历史第 1 版正文。"
    assert saved_document.updated_by == user_id
    assert [
        item.version
        for item in saved_versions
    ] == [
        1,
        2,
        3,
    ]
    assert saved_versions[2].title == "当前第 3 版"
    provider.embed_texts.assert_called_once()
