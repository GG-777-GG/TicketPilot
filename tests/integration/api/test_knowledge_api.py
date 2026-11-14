"""
测试知识文档入库 API 的 HTTP 边界。

测试范围：
- 422：请求数据不合法。
- 401：没有登录。
- 403：普通 agent 权限不足。
- 201：主管或管理员入库成功。
- 500：入库服务发生内部错误。
"""

# Generator 用来标注包含 yield 的测试依赖。
from collections.abc import Generator

# 固定响应中的测试时间。
from datetime import (
    UTC,
    datetime,
)

# MagicMock 用来创建 Fake Embedding Provider。
from unittest.mock import MagicMock

# pytest 用来创建自动清理 fixture。
import pytest

# TestClient 模拟前端发送 HTTP 请求。
from fastapi.testclient import TestClient

# Session 用于测试数据库依赖的类型注解。
from sqlalchemy import select
from sqlalchemy.orm import Session

from ticketpilot.knowledge.persistence.models import (
    KnowledgeChunkRecord,
    KnowledgeDocumentRecord,
    KnowledgeDocumentVersionRecord,
)

# 导入列表测试需要的 Repository 返回类型。
from ticketpilot.knowledge.persistence.repository import (
    KnowledgeDocumentDetailRow,
    KnowledgeDocumentListPage,
    KnowledgeDocumentSummaryRow,
    KnowledgeDocumentVersionDetailRow,
    KnowledgeDocumentVersionListPage,
    KnowledgeDocumentVersionSummaryRow,
    KnowledgeRepositoryError,
)

# 导入知识文档和片段测试工厂。
from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
    build_knowledge_document_version,
)

# 导入 FastAPI 应用。
from ticketpilot.app import app

# 导入真正的 Session 依赖函数。
from ticketpilot.core.database import (
    get_session,
)

# 导入真正的当前用户依赖函数。
from ticketpilot.identity.dependencies import (
    get_current_user,
)

# 导入用户 ORM 类型。
from ticketpilot.identity.models import (
    UserRecord,
)

# 导入用户角色类型。
from ticketpilot.identity.types import (
    UserRole,
)

# 导入固定向量维度。
from ticketpilot.knowledge.domain.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入真正的 Embedding Provider 依赖工厂。
from ticketpilot.knowledge.api.dependencies import (
    get_embedding_provider,
)

# 导入统一的 Embedding 接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
)

# 导入入库服务统一异常。
from ticketpilot.knowledge.application.ingestion import (
    KnowledgeIngestionError,
)

# 导入知识来源枚举。
from ticketpilot.knowledge.domain.documents import (
    KnowledgeSourceType,
)

# 导入检索服务返回的内部结果类型。
from ticketpilot.knowledge.application.retrieval import (
    KnowledgeRetrievalError,
    RetrievedKnowledgeChunk,
)

# 导入知识库 Router 模块。
# 后面的测试会替换它实际调用的 ingest_document。
import ticketpilot.knowledge.api.router as knowledge_api


# 创建模拟 HTTP 客户端。
client = TestClient(
    app
)


# 使用固定时间，避免测试结果每次发生变化。
TEST_TIMESTAMP = datetime(
    2026,
    1,
    1,
    tzinfo=UTC,
)


# 每项测试前后都清除 FastAPI 依赖覆盖。
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


# 这个函数负责提供 API 测试使用的空数据库 Session。
def fake_get_session() -> Generator[
    Session,
    None,
    None,
]:
    # 当前 HTTP 边界测试不会执行真实 SQL。
    with Session() as session:
        yield session


# 这个函数负责构造指定角色的测试用户。
def build_test_user(
    role: UserRole,
) -> UserRecord:
    return UserRecord(
        id=f"{role}-knowledge-api-user",
        email=(
            f"{role}-knowledge-api@example.com"
        ),
        display_name="知识库 API 测试用户",
        password_hash="not-used-in-test",
        role=role,
        is_active=True,
        created_at=TEST_TIMESTAMP,
    )


# 这个函数负责替换认证、Session 和向量模型依赖。
def configure_dependencies(
    *,
    current_user: UserRecord,
    embedding_provider: EmbeddingProvider,
) -> None:
    # 测试时直接返回指定用户，不验证真实 JWT。
    def fake_get_current_user() -> UserRecord:
        return current_user

    # 测试时返回 Fake Provider，不连接 Ollama。
    def fake_get_embedding_provider() -> (
        EmbeddingProvider
    ):
        return embedding_provider

    app.dependency_overrides[
        get_session
    ] = fake_get_session

    app.dependency_overrides[
        get_current_user
    ] = fake_get_current_user

    app.dependency_overrides[
        get_embedding_provider
    ] = fake_get_embedding_provider


# 验证主管可以使用默认分页查询知识文档列表。
def test_list_knowledge_documents_returns_default_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 创建拥有知识库管理权限的主管用户。
    current_user = build_test_user(
        "supervisor"
    )

    # 列表接口不会调用向量模型，
    # 但公共依赖配置函数仍然需要这个参数。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 保存 Router 传给 Repository 的参数。
    received_calls: list[
        dict[str, object]
    ] = []

    # 模拟 Repository 返回一页知识文档摘要。
    def fake_list_documents(
        session: Session,
        *,
        limit: int,
        offset: int,
    ) -> KnowledgeDocumentListPage:
        # 记录 Router 是否正确传递参数。
        received_calls.append(
            {
                "session": session,
                "limit": limit,
                "offset": offset,
            }
        )

        return KnowledgeDocumentListPage(
            items=(
                KnowledgeDocumentSummaryRow(
                    id="document-001",
                    title="密码重置说明",
                    source_type="manual",
                    source_uri=None,
                    status="ready",
                    visibility="restricted",
                    chunk_count=2,
                    embedding_model=(
                        "qwen3-embedding:0.6b"
                    ),
                    embedding_dimensions=(
                        EMBEDDING_DIMENSIONS
                    ),
                    version=1,
                    created_by=current_user.id,
                    created_at=TEST_TIMESTAMP,
                    updated_at=TEST_TIMESTAMP,
                ),
            ),
            total=1,
        )

    # 只替换 Repository 查询，
    # Router、权限验证和响应转换仍使用真实代码。
    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_documents",
        fake_list_documents,
    )

    # 不传 limit 和 offset，验证默认分页参数。
    response = client.get(
        "/v1/knowledge/documents"
    )

    # 查询成功应该返回 HTTP 200。
    assert response.status_code == 200

    # 验证完整公开响应。
    assert response.json() == {
        "items": [
            {
                "id": "document-001",
                "title": "密码重置说明",
                "source_type": "manual",
                "source_uri": None,
                "status": "ready",
                "visibility": "restricted",
                "chunk_count": 2,
                "embedding_model": (
                    "qwen3-embedding:0.6b"
                ),
                "embedding_dimensions": (
                    EMBEDDING_DIMENSIONS
                ),
                "version": 1,
                "created_by": current_user.id,
                "created_at": (
                    "2026-01-01T00:00:00Z"
                ),
                "updated_at": (
                    "2026-01-01T00:00:00Z"
                ),
            },
        ],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }

    # Repository 应该只执行一次。
    assert len(
        received_calls
    ) == 1

    repository_call = received_calls[0]

    # Router 应该使用默认分页参数。
    assert repository_call["limit"] == 20
    assert repository_call["offset"] == 0

    # Router 应该传入依赖注入提供的 Session。
    assert isinstance(
        repository_call["session"],
        Session,
    )

# 验证列表接口可以接收自定义分页，并正常返回空页面。
def test_list_knowledge_documents_accepts_custom_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 管理员拥有知识文档管理权限。
    current_user = build_test_user(
        "admin"
    )

    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 只保存 Repository 收到的分页参数。
    received_pagination: list[
        tuple[
            int,
            int,
        ]
    ] = []

    def fake_list_documents(
        session: Session,
        *,
        limit: int,
        offset: int,
    ) -> KnowledgeDocumentListPage:
        # Router 必须传入依赖注入得到的 Session。
        assert isinstance(
            session,
            Session,
        )

        received_pagination.append(
            (
                limit,
                offset,
            )
        )

        # 数据库一共有 3 篇文档，
        # offset=3 表示已经跳过全部文档。
        return KnowledgeDocumentListPage(
            items=(),
            total=3,
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_documents",
        fake_list_documents,
    )

    # 自定义每页数量和偏移量。
    response = client.get(
        (
            "/v1/knowledge/documents"
            "?limit=2"
            "&offset=3"
        )
    )

    # 没有当前页数据不是错误，仍然返回 200。
    assert response.status_code == 200

    assert response.json() == {
        "items": [],
        "total": 3,
        "limit": 2,
        "offset": 3,
    }

    # Router 应该把客户端参数原样传给 Repository。
    assert received_pagination == [
        (
            2,
            3,
        )
    ]

# 验证未登录用户不能查询知识文档列表。
def test_list_knowledge_documents_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 只提供测试 Session，
    # 故意不覆盖 get_current_user。
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    # 未登录时绝对不能进入 Repository。
    def should_not_list_documents(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "未登录用户不应该查询知识文档列表"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_documents",
        should_not_list_documents,
    )

    # 请求故意不携带 Authorization 请求头。
    response = client.get(
        "/v1/knowledge/documents"
    )

    # 没有通过身份认证应该返回 401。
    assert response.status_code == 401

    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }

# 验证普通 agent 不能管理知识文档列表。
def test_list_knowledge_documents_forbids_agent_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 创建已经登录但权限不足的普通客服用户。
    current_user = build_test_user(
        "agent"
    )

    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 角色检查失败后绝对不能进入 Repository。
    def should_not_list_documents(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "普通 agent 不应该查询知识文档列表"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_documents",
        should_not_list_documents,
    )

    response = client.get(
        "/v1/knowledge/documents"
    )

    # 用户已经登录，但角色不允许，因此返回 403。
    assert response.status_code == 403

    assert response.json() == {
        "detail": "权限不足",
    }

@pytest.mark.parametrize(
    "invalid_query",
    [
        pytest.param(
            "limit=0",
            id="limit_too_small",
        ),
        pytest.param(
            "limit=101",
            id="limit_too_large",
        ),
        pytest.param(
            "offset=-1",
            id="negative_offset",
        ),
        pytest.param(
            "limit=abc",
            id="limit_not_integer",
        ),
    ],
)
# 验证非法分页参数会在进入 Repository 前返回 422。
def test_list_knowledge_documents_rejects_invalid_pagination(
    monkeypatch: pytest.MonkeyPatch,
    invalid_query: str,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )

    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # Query 参数验证失败时不能执行数据库查询。
    def should_not_list_documents(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "非法分页参数不应该进入 Repository"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_documents",
        should_not_list_documents,
    )

    response = client.get(
        (
            "/v1/knowledge/documents"
            f"?{invalid_query}"
        )
    )

    # FastAPI 的 Query 验证失败。
    assert response.status_code == 422
    assert "detail" in response.json()

# 验证 Repository 故障会转换成安全的 HTTP 500。
def test_list_knowledge_documents_returns_500_when_repository_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )

    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 模拟数据库连接或 SQL 查询失败。
    def failing_list_documents(
        *args,
        **kwargs,
    ):
        raise KnowledgeRepositoryError(
            "这里是不能泄漏给客户端的数据库错误"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_documents",
        failing_list_documents,
    )

    response = client.get(
        "/v1/knowledge/documents"
    )

    # Router 应该返回统一的服务器错误。
    assert response.status_code == 500

    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }

    # 客户端不能看到 SQL 或数据库内部错误。
    assert (
        "不能泄漏"
        not in response.text
    )


# 这个测试负责验证主管可以根据 ID 查询知识文档详情。
def test_get_knowledge_document_returns_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    received_arguments: dict[
        str,
        object,
    ] = {}

    # 模拟 Repository 找到一篇知识文档。
    def fake_get_document_by_id(
        session: Session,
        document_id: str,
    ) -> KnowledgeDocumentDetailRow:
        received_arguments["session"] = session
        received_arguments["document_id"] = document_id

        return KnowledgeDocumentDetailRow(
            id="document-001",
            title="密码重置说明",
            raw_content="  用户可以重置密码。  ",
            cleaned_content="用户可以重置密码。",
            source_type="manual",
            source_uri=None,
            status="ready",
            visibility="restricted",
            processing_stage=None,
            chunk_count=1,
            embedding_model="qwen3-embedding:0.6b",
            embedding_dimensions=1024,
            cleaner_version="cleaner-v1",
            chunker_version="chunker-v1",
            version=1,
            created_by="user-001",
            created_at=TEST_TIMESTAMP,
            updated_at=TEST_TIMESTAMP,
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_by_id",
        fake_get_document_by_id,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001"
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": "document-001",
        "title": "密码重置说明",
        "source_type": "manual",
        "source_uri": None,
        "status": "ready",
        "visibility": "restricted",
        "chunk_count": 1,
        "embedding_model": "qwen3-embedding:0.6b",
        "embedding_dimensions": 1024,
        "version": 1,
        "created_by": "user-001",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "raw_content": "  用户可以重置密码。  ",
        "cleaned_content": "用户可以重置密码。",
        "processing_stage": None,
        "cleaner_version": "cleaner-v1",
        "chunker_version": "chunker-v1",
    }
    assert received_arguments["document_id"] == "document-001"
    assert isinstance(
        received_arguments["session"],
        Session,
    )


# 这个测试负责验证不存在的文档会返回 404。
def test_get_knowledge_document_returns_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_by_id",
        lambda session, document_id: None,
    )

    response = client.get(
        "/v1/knowledge/documents/missing-document"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "知识文档不存在",
    }


# 这个测试负责验证未登录用户不能查看知识文档详情。
def test_get_knowledge_document_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    def should_not_query_repository(
        session: Session,
        document_id: str,
    ) -> None:
        raise AssertionError(
            "未登录用户不应该查询知识文档详情"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_by_id",
        should_not_query_repository,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001"
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


# 这个测试负责验证普通客服不能查看知识文档详情。
def test_get_knowledge_document_forbids_agent_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "agent"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_query_repository(
        session: Session,
        document_id: str,
    ) -> None:
        raise AssertionError(
            "普通客服不应该查询知识文档详情"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_by_id",
        should_not_query_repository,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001"
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "权限不足",
    }


# 这个测试负责验证过长的文档 ID 会在进入 Repository 前返回 422。
def test_get_knowledge_document_rejects_invalid_document_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_query_repository(
        session: Session,
        document_id: str,
    ) -> None:
        raise AssertionError(
            "非法文档 ID 不应该进入 Repository"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_by_id",
        should_not_query_repository,
    )

    response = client.get(
        f"/v1/knowledge/documents/{'x' * 37}"
    )

    assert response.status_code == 422
    assert "detail" in response.json()


# 这个测试负责验证 Repository 错误会转换成安全的 HTTP 500。
def test_get_knowledge_document_returns_500_when_repository_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def failing_get_document_by_id(
        session: Session,
        document_id: str,
    ) -> None:
        raise KnowledgeRepositoryError(
            "这里是不能泄漏给客户端的数据库错误"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_by_id",
        failing_get_document_by_id,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001"
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }
    assert "不能泄漏" not in response.text


# 这个测试负责验证主管可以成功归档知识文档。
def test_archive_knowledge_document_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    received_arguments: dict[
        str,
        object,
    ] = {}

    def fake_archive_document(
        *,
        session: Session,
        document_id: str,
    ) -> bool:
        received_arguments["session"] = session
        received_arguments["document_id"] = document_id
        return True

    monkeypatch.setattr(
        knowledge_api,
        "archive_document",
        fake_archive_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/archive"
    )

    assert response.status_code == 204
    assert response.content == b""
    assert received_arguments["document_id"] == "document-001"
    assert isinstance(
        received_arguments["session"],
        Session,
    )


# 这个测试负责验证归档不存在的文档会返回 404。
def test_archive_knowledge_document_returns_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    monkeypatch.setattr(
        knowledge_api,
        "archive_document",
        lambda **kwargs: False,
    )

    response = client.post(
        "/v1/knowledge/documents/missing-document/archive"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "知识文档不存在",
    }


# 这个测试负责验证未登录用户不能归档知识文档。
def test_archive_knowledge_document_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    def should_not_archive_document(
        **kwargs,
    ) -> bool:
        raise AssertionError(
            "未登录用户不应该进入归档 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "archive_document",
        should_not_archive_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/archive"
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


# 这个测试负责验证普通客服不能归档知识文档。
def test_archive_knowledge_document_forbids_agent_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "agent"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_archive_document(
        **kwargs,
    ) -> bool:
        raise AssertionError(
            "普通客服不应该进入归档 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "archive_document",
        should_not_archive_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/archive"
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "权限不足",
    }


# 这个测试负责验证过长文档 ID 会返回 422。
def test_archive_knowledge_document_rejects_invalid_document_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_archive_document(
        **kwargs,
    ) -> bool:
        raise AssertionError(
            "非法文档 ID 不应该进入归档 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "archive_document",
        should_not_archive_document,
    )

    response = client.post(
        f"/v1/knowledge/documents/{'x' * 37}/archive"
    )

    assert response.status_code == 422
    assert "detail" in response.json()


# 这个测试负责验证归档失败会返回安全的 HTTP 500。
def test_archive_knowledge_document_returns_500_when_service_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def failing_archive_document(
        **kwargs,
    ) -> bool:
        raise knowledge_api.KnowledgeManagementError(
            "这里是不能泄漏给客户端的内部错误"
        )

    monkeypatch.setattr(
        knowledge_api,
        "archive_document",
        failing_archive_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/archive"
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }
    assert "不能泄漏" not in response.text

@pytest.mark.parametrize(
    "allowed_role",
    [
        pytest.param(
            "supervisor",
            id="supervisor",
        ),
        pytest.param(
            "admin",
            id="admin",
        ),
    ],
)

@pytest.mark.parametrize(
    ("requested_visibility", "expected_visibility"),
    [
        (None, "internal"),
        ("restricted", "restricted"),
    ],
)
# 这个测试负责验证主管能够成功创建知识文档。
def test_create_knowledge_document_returns_201(
    monkeypatch,
    allowed_role: UserRole,
    requested_visibility: str | None,
    expected_visibility: str,
) -> None:
    # 创建主管用户。
    current_user = build_test_user(
        allowed_role
    )

    # 创建不会连接真实 Ollama 的 Fake Provider。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 配置当前测试使用的依赖。
    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 保存 Router 交给入库服务的参数。
    received_calls: list[
        dict[str, object]
    ] = []

    # 模拟完整入库服务成功返回文档。
    def fake_ingest_document(
        **kwargs,
    ):
        received_calls.append(
            kwargs
        )

        # 创建模拟的文档 ORM 对象。
        document = build_knowledge_document(
            document_id="knowledge-doc-001",
            created_by=kwargs["created_by"],
            title="TicketPilot 密码重置说明",
            raw_content=(
                "用户可以通过邮箱重置密码。"
            ),
            embedding_model="fake-embedding-model",
        )

        # 模拟数据库和业务层生成的字段。
        document.source_type = "manual"
        document.source_uri = None
        document.status = "ready"
        document.visibility = kwargs["visibility"]
        document.embedding_dimensions = (
            EMBEDDING_DIMENSIONS
        )
        document.version = 1
        document.created_at = TEST_TIMESTAMP
        document.updated_at = TEST_TIMESTAMP

        # 模拟入库后生成两个知识片段。
        document.chunks.extend(
            [
                build_knowledge_chunk(
                    chunk_index=0,
                    content="用户可以通过邮箱",
                    start_offset=0,
                    end_offset=8,
                ),
                build_knowledge_chunk(
                    chunk_index=1,
                    content="通过邮箱重置密码。",
                    start_offset=5,
                    end_offset=14,
                ),
            ]
        )

        return document

    # Router 中调用 ingest_document 时，
    # 改为执行上面的测试函数。
    monkeypatch.setattr(
        knowledge_api,
        "ingest_document",
        fake_ingest_document,
    )

    # 模拟前端提交一篇手动知识文档。
    request_json = {
        "title": "TicketPilot 密码重置说明",
        "content": "用户可以通过邮箱重置密码。",
    }
    if requested_visibility is not None:
        request_json["visibility"] = requested_visibility

    response = client.post(
        "/v1/knowledge/documents",
        json=request_json,
    )

    # 创建成功应该返回 HTTP 201。
    assert response.status_code == 201

    # 读取返回的 JSON。
    response_data = response.json()

    # 验证公开响应中的核心字段。
    assert (
        response_data["id"]
        == "knowledge-doc-001"
    )
    assert (
        response_data["status"]
        == "ready"
    )
    assert response_data["visibility"] == expected_visibility
    assert (
        response_data["chunk_count"]
        == 2
    )
    assert (
        response_data["embedding_model"]
        == "fake-embedding-model"
    )
    assert (
        response_data["embedding_dimensions"]
        == EMBEDDING_DIMENSIONS
    )
    assert (
        response_data["created_by"]
        == current_user.id
    )

    # 响应不能泄漏文档正文、指纹或向量。
    assert "raw_content" not in response_data
    assert "cleaned_content" not in response_data
    assert "content_hash" not in response_data
    assert "embedding" not in response_data

    # 入库服务应该只被调用一次。
    assert len(
        received_calls
    ) == 1

    # created_by 必须来自当前登录用户。
    assert (
        received_calls[0]["created_by"]
        == current_user.id
    )

    # 创建时就把可见性传给入库服务。
    assert received_calls[0]["visibility"] == expected_visibility

    # Router 必须把依赖注入的 Provider 原样传给 Service。
    assert (
        received_calls[0]["embedding_provider"]
        is embedding_provider
    )

    # Router 应该把请求转换成 RawDocument。
    raw_document = (
        received_calls[0]["raw_document"]
    )
    assert raw_document.title == (
        "TicketPilot 密码重置说明"
    )
    assert raw_document.content == (
        "用户可以通过邮箱重置密码。"
    )

# 这个测试负责验证非法请求会在进入入库服务前被拒绝。
def test_create_knowledge_document_returns_422_for_invalid_request(
    monkeypatch,
) -> None:
    # 使用有权限的主管，确保失败原因只来自请求数据。
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 如果这个函数被执行，
    # 说明非法请求没有被 Pydantic 拦截。
    def should_not_ingest_document(
        **kwargs,
    ):
        raise AssertionError(
            "非法请求不应该进入知识入库服务"
        )

    monkeypatch.setattr(
        knowledge_api,
        "ingest_document",
        should_not_ingest_document,
    )

    # 正文只包含空白字符。
    response = client.post(
        "/v1/knowledge/documents",
        json={
            "title": "无效知识文档",
            "content": " \t\n ",
        },
    )

    # Pydantic 验证失败应该返回 422。
    assert response.status_code == 422

    # 响应应该包含字段验证错误。
    response_data = response.json()
    assert "detail" in response_data


# 这个测试负责验证未登录用户不能创建知识文档。
def test_create_knowledge_document_requires_authentication(
    monkeypatch,
) -> None:
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 只覆盖 Session 和 Provider，
    # 故意不覆盖 get_current_user。
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    app.dependency_overrides[
        get_embedding_provider
    ] = lambda: embedding_provider

    # 未登录时绝对不能进入入库服务。
    def should_not_ingest_document(
        **kwargs,
    ):
        raise AssertionError(
            "未登录用户不应该进入知识入库服务"
        )

    monkeypatch.setattr(
        knowledge_api,
        "ingest_document",
        should_not_ingest_document,
    )

    # 请求故意不携带 Authorization 请求头。
    response = client.post(
        "/v1/knowledge/documents",
        json={
            "title": "密码重置说明",
            "content": "用户可以通过邮箱重置密码。",
        },
    )

    # 没有通过身份认证应该返回 401。
    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


# 这个测试负责验证普通 agent 不能修改企业知识库。
def test_create_knowledge_document_forbids_agent_role(
    monkeypatch,
) -> None:
    # 创建已经登录但权限不足的普通客服用户。
    current_user = build_test_user(
        "agent"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 权限检查失败后不能进入入库服务。
    def should_not_ingest_document(
        **kwargs,
    ):
        raise AssertionError(
            "普通 agent 不应该进入知识入库服务"
        )

    monkeypatch.setattr(
        knowledge_api,
        "ingest_document",
        should_not_ingest_document,
    )

    response = client.post(
        "/v1/knowledge/documents",
        json={
            "title": "密码重置说明",
            "content": "用户可以通过邮箱重置密码。",
        },
    )

    # 用户已经登录，但角色不允许，因此返回 403。
    assert response.status_code == 403
    assert response.json() == {
        "detail": "权限不足",
    }

# 这个测试负责验证入库服务错误会转换成安全的 HTTP 500。
def test_create_knowledge_document_returns_500_when_ingestion_fails(
    monkeypatch,
) -> None:
    # 使用具有权限的主管用户。
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 模拟入库服务发生内部错误。
    def failing_ingest_document(
        **kwargs,
    ):
        raise KnowledgeIngestionError(
            "这里是不能泄漏给客户端的内部错误"
        )

    monkeypatch.setattr(
        knowledge_api,
        "ingest_document",
        failing_ingest_document,
    )

    response = client.post(
        "/v1/knowledge/documents",
        json={
            "title": "密码重置说明",
            "content": "用户可以通过邮箱重置密码。",
        },
    )

    # Router 应该转换成稳定的服务器错误。
    assert response.status_code == 500
    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }

    # 客户端响应中不能出现底层错误内容。
    assert (
        "不能泄漏"
        not in response.text
    )

# 这个测试负责验证 HTTP 请求能够通过真实业务层写入数据库。
def test_create_knowledge_document_persists_through_full_http_flow(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    # database_user 默认是 admin，拥有知识库管理权限。
    database_session.commit()
    user_id = database_user.id

    # 创建 Fake Provider，但不替换真实 Ingestion Service。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 根据实际片段数量动态返回等量向量。
    def fake_embed_texts(
        texts,
    ) -> EmbeddingBatch:
        return EmbeddingBatch(
            model_name="fake-embedding-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=tuple(
                tuple(
                    [0.1]
                    * EMBEDDING_DIMENSIONS
                )
                for _ in texts
            ),
        )

    embedding_provider.embed_texts.side_effect = (
        fake_embed_texts
    )

    # 让 FastAPI 使用 fixture 提供的真实 SQLite Session。
    def use_database_session() -> Generator[
        Session,
        None,
        None,
    ]:
        yield database_session

    # 模拟 JWT 已经验证出数据库中的管理员用户。
    def use_database_user() -> UserRecord:
        return database_user

    # 返回 Fake Provider，避免连接真实 Ollama。
    def use_embedding_provider() -> (
        EmbeddingProvider
    ):
        return embedding_provider

    app.dependency_overrides[
        get_session
    ] = use_database_session

    app.dependency_overrides[
        get_current_user
    ] = use_database_user

    app.dependency_overrides[
        get_embedding_provider
    ] = use_embedding_provider

    # 1000 个字符按照默认 800/100 规则会生成两个片段。
    original_content = (
        "知识库内容" * 200
    )

    # 发送真实 HTTP 请求。
    response = client.post(
        "/v1/knowledge/documents",
        json={
            "title": "ＴｉｃｋｅｔＰｉｌｏｔ 完整入库测试",
            "content": original_content,
        },
    )

    # HTTP 接口应该创建成功。
    assert response.status_code == 201

    response_data = response.json()
    assert response_data["status"] == "ready"
    assert response_data["chunk_count"] == 2
    assert response_data["created_by"] == user_id

    # 清除 ORM 缓存，确保后面重新查询数据库。
    database_session.expire_all()

    # 根据接口返回的 ID 重新读取数据库。
    stored_document = database_session.get(
        KnowledgeDocumentRecord,
        response_data["id"],
    )

    assert stored_document is not None

    # 标题应该已经经过 NFKC 清洗。
    assert stored_document.title == (
        "TicketPilot 完整入库测试"
    )

    # 原始正文应该完整保存。
    assert (
        stored_document.raw_content
        == original_content
    )

    # 文档与两个片段都已真正写入数据库。
    assert len(
        stored_document.chunks
    ) == 2

    # 每个片段都应该拥有 1024 维向量。
    assert all(
        len(chunk.embedding)
        == EMBEDDING_DIMENSIONS
        for chunk in stored_document.chunks
    )

    # Fake Provider 应该只收到一次批量调用。
    embedding_provider.embed_texts.assert_called_once()

# 这个测试负责验证已登录用户能够成功检索知识，并返回来源页码。
@pytest.mark.parametrize(
    ("source_type", "source_uri", "page_numbers"),
    [
        # 手动输入的文档没有 PDF 页码。
        (KnowledgeSourceType.MANUAL, None, ()),
        # PDF 片段可能跨页，公开响应应返回两个原始页码。
        (KnowledgeSourceType.FILE, "guide.pdf", (2, 4)),
    ],
)
def test_search_knowledge_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    source_type: KnowledgeSourceType,
    source_uri: str | None,
    page_numbers: tuple[int, ...],
) -> None:
    # 普通客服用户也允许查询企业知识库。
    current_user = build_test_user(
        "agent"
    )

    # 创建 Fake Provider，避免连接真实 Ollama。
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 覆盖认证、Session 和向量模型依赖。
    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 保存 Router 传给 Retrieval Service 的参数。
    received_calls: list[
        dict[str, object]
    ] = []

    # 模拟 Retrieval Service 返回一条相关知识片段。
    def fake_retrieve_knowledge(
        **kwargs,
    ) -> tuple[
        RetrievedKnowledgeChunk,
        ...,
    ]:
        received_calls.append(
            kwargs
        )

        return (
            RetrievedKnowledgeChunk(
                chunk_id="chunk-001",
                document_id="document-001",
                document_title="密码重置说明",
                document_version=1,
                source_type=source_type,
                source_uri=source_uri,
                chunk_index=0,
                content=(
                    "用户可以通过注册邮箱重置密码。"
                ),
                start_offset=0,
                end_offset=17,
                page_numbers=page_numbers,

                # similarity = 1 - 0.08 = 0.92
                cosine_distance=0.08,
            ),
        )

    # 临时替换 Router 实际调用的检索服务。
    monkeypatch.setattr(
        knowledge_api,
        "retrieve_knowledge",
        fake_retrieve_knowledge,
    )

    # 模拟前端发送知识检索请求。
    response = client.post(
        "/v1/knowledge/search",
        json={
            # 故意保留首尾空格，
            # 验证响应是否返回清理后的问题。
            "query": "  如何重置密码？  ",
            "top_k": 3,
            "min_similarity": 0.5,
        },
    )

    # 检索成功应该返回 HTTP 200。
    assert response.status_code == 200

    # 验证公开响应内容。
    assert response.json() == {
        "query": "如何重置密码？",
        "result_count": 1,
        "results": [
            {
                "chunk_id": "chunk-001",
                "document_id": "document-001",
                "document_title": "密码重置说明",
                "document_version": 1,
                "source_type": source_type.value,
                "source_uri": source_uri,
                "chunk_index": 0,
                "content": (
                    "用户可以通过注册邮箱重置密码。"
                ),
                "start_offset": 0,
                "end_offset": 17,
                "page_numbers": list(page_numbers),
                "similarity": 0.92,
            },
        ],
    }

    # Retrieval Service 应该只被调用一次。
    assert len(
        received_calls
    ) == 1

    service_call = received_calls[0]

    # Router 应该传递请求中的原始问题，
    # 具体的规范化属于 Service 的职责。
    assert service_call["question"] == (
        "  如何重置密码？  "
    )

    assert service_call["top_k"] == 3
    assert (
        service_call["min_similarity"]
        == 0.5
    )

    # Router 必须传递依赖注入得到的 Provider。
    assert (
        service_call["embedding_provider"]
        is embedding_provider
    )

    # Router 必须向 Service 传入数据库 Session。
    assert isinstance(
        service_call["session"],
        Session,
    )

# 这个测试负责验证路由按登录角色传递知识文档可见范围。
@pytest.mark.parametrize(
    ("role", "expected_visibilities"),
    [
        ("agent", ("internal",)),
        ("supervisor", ("internal", "restricted")),
        ("admin", ("internal", "restricted")),
    ],
)
def test_search_knowledge_uses_current_user_visibility(
    monkeypatch: pytest.MonkeyPatch,
    role: UserRole,
    expected_visibilities: tuple[str, ...],
) -> None:
    # 测试认证依赖返回的角色，不能信任请求自行声明权限。
    current_user = build_test_user(role)
    embedding_provider = MagicMock(spec=EmbeddingProvider)
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )
    retrieve_mock = MagicMock(return_value=())
    monkeypatch.setattr(
        knowledge_api,
        "retrieve_knowledge",
        retrieve_mock,
    )

    response = client.post(
        "/v1/knowledge/search",
        json={"query": "如何重置密码？"},
    )

    assert response.status_code == 200
    retrieve_mock.assert_called_once()
    assert retrieve_mock.call_args.kwargs[
        "allowed_visibilities"
    ] == expected_visibilities


# 这个测试负责验证没有匹配内容时返回空结果。
def test_search_knowledge_returns_empty_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "agent"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 模拟 Retrieval Service 没有找到相关内容。
    monkeypatch.setattr(
        knowledge_api,
        "retrieve_knowledge",
        lambda **kwargs: (),
    )

    response = client.post(
        "/v1/knowledge/search",
        json={
            "query": "知识库中不存在的问题",
        },
    )

    # 没有结果不是系统错误，仍然返回 200。
    assert response.status_code == 200

    assert response.json() == {
        "query": "知识库中不存在的问题",
        "result_count": 0,
        "results": [],
    }


# 这个测试负责验证未登录用户不能检索知识。
def test_search_knowledge_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    # 只覆盖数据库和 Provider，
    # 故意不覆盖 get_current_user。
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    app.dependency_overrides[
        get_embedding_provider
    ] = lambda: embedding_provider

    # 未登录时不能进入 Retrieval Service。
    def should_not_retrieve_knowledge(
        **kwargs,
    ):
        raise AssertionError(
            "未登录用户不应该进入知识检索服务"
        )

    monkeypatch.setattr(
        knowledge_api,
        "retrieve_knowledge",
        should_not_retrieve_knowledge,
    )

    response = client.post(
        "/v1/knowledge/search",
        json={
            "query": "如何重置密码？",
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


@pytest.mark.parametrize(
    "invalid_request",
    [
        pytest.param(
            {
                "query": "   ",
            },
            id="blank_query",
        ),
        pytest.param(
            {
                "query": "如何重置密码？",
                "top_k": 0,
            },
            id="invalid_top_k",
        ),
        pytest.param(
            {
                "query": "如何重置密码？",
                "min_similarity": 1.01,
            },
            id="invalid_similarity",
        ),
    ],
)
# 这个测试负责验证非法检索请求会返回 422。
def test_search_knowledge_returns_422_for_invalid_request(
    monkeypatch: pytest.MonkeyPatch,
    invalid_request: dict[
        str,
        object,
    ],
) -> None:
    current_user = build_test_user(
        "agent"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # Pydantic 应该在进入 Service 前拒绝请求。
    def should_not_retrieve_knowledge(
        **kwargs,
    ):
        raise AssertionError(
            "非法请求不应该进入知识检索服务"
        )

    monkeypatch.setattr(
        knowledge_api,
        "retrieve_knowledge",
        should_not_retrieve_knowledge,
    )

    response = client.post(
        "/v1/knowledge/search",
        json=invalid_request,
    )

    assert response.status_code == 422
    assert "detail" in response.json()

# 这个测试负责验证检索服务错误会转换成安全的 HTTP 500。
def test_search_knowledge_returns_500_when_retrieval_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "agent"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    configure_dependencies(
        current_user=current_user,
        embedding_provider=(
            embedding_provider
        ),
    )

    # 模拟 Ollama 或数据库导致检索服务失败。
    def failing_retrieve_knowledge(
        **kwargs,
    ):
        raise KnowledgeRetrievalError(
            "这里是不能泄漏给客户端的内部错误"
        )

    monkeypatch.setattr(
        knowledge_api,
        "retrieve_knowledge",
        failing_retrieve_knowledge,
    )

    response = client.post(
        "/v1/knowledge/search",
        json={
            "query": "如何重置密码？",
        },
    )

    # Router 应该转换成稳定的服务器错误。
    assert response.status_code == 500

    assert response.json() == {
        "detail": "知识检索服务暂时不可用",
    }

    # 客户端不能看到 Ollama、SQL 等内部错误。
    assert (
        "不能泄漏"
        not in response.text
    )


# 这个测试负责验证主管可以恢复已归档知识文档。
def test_restore_knowledge_document_returns_204(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    received_arguments: dict[
        str,
        object,
    ] = {}

    def fake_restore_document(
        *,
        session: Session,
        document_id: str,
    ) -> bool:
        received_arguments["session"] = session
        received_arguments["document_id"] = document_id
        return True

    monkeypatch.setattr(
        knowledge_api,
        "restore_document",
        fake_restore_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/restore"
    )

    assert response.status_code == 204
    assert response.content == b""
    assert received_arguments["document_id"] == "document-001"
    assert isinstance(
        received_arguments["session"],
        Session,
    )


# 这个测试负责验证恢复不存在的文档会返回 404。
def test_restore_knowledge_document_returns_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )
    monkeypatch.setattr(
        knowledge_api,
        "restore_document",
        lambda **kwargs: False,
    )

    response = client.post(
        "/v1/knowledge/documents/missing-document/restore"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "知识文档不存在",
    }


# 这个测试负责验证不允许恢复的文档状态会返回 409。
def test_restore_knowledge_document_returns_409_for_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def failing_restore_document(
        **kwargs,
    ) -> bool:
        raise knowledge_api.InvalidKnowledgeDocumentStateError(
            "只有已归档的知识文档可以恢复"
        )

    monkeypatch.setattr(
        knowledge_api,
        "restore_document",
        failing_restore_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/restore"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "只有已归档的知识文档可以恢复",
    }


# 这个测试负责验证未登录用户不能恢复知识文档。
def test_restore_knowledge_document_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    def should_not_restore_document(
        **kwargs,
    ) -> bool:
        raise AssertionError(
            "未登录用户不应该进入恢复 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "restore_document",
        should_not_restore_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/restore"
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


# 这个测试负责验证普通客服不能恢复知识文档。
def test_restore_knowledge_document_forbids_agent_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "agent"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_restore_document(
        **kwargs,
    ) -> bool:
        raise AssertionError(
            "普通客服不应该进入恢复 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "restore_document",
        should_not_restore_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/restore"
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "权限不足",
    }


# 这个测试负责验证过长文档 ID 会返回 422。
def test_restore_knowledge_document_rejects_invalid_document_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_restore_document(
        **kwargs,
    ) -> bool:
        raise AssertionError(
            "非法文档 ID 不应该进入恢复 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "restore_document",
        should_not_restore_document,
    )

    response = client.post(
        f"/v1/knowledge/documents/{'x' * 37}/restore"
    )

    assert response.status_code == 422
    assert "detail" in response.json()


# 这个测试负责验证恢复失败会返回安全的 HTTP 500。
def test_restore_knowledge_document_returns_500_when_service_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def failing_restore_document(
        **kwargs,
    ) -> bool:
        raise knowledge_api.KnowledgeManagementError(
            "这里是不能泄漏给客户端的内部错误"
        )

    monkeypatch.setattr(
        knowledge_api,
        "restore_document",
        failing_restore_document,
    )

    response = client.post(
        "/v1/knowledge/documents/document-001/restore"
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }
    assert "不能泄漏" not in response.text


@pytest.mark.parametrize(
    "allowed_role",
    [
        pytest.param(
            "supervisor",
            id="supervisor",
        ),
        pytest.param(
            "admin",
            id="admin",
        ),
    ],
)
# 这个测试负责验证主管和管理员能够更新知识文档。
def test_update_knowledge_document_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    allowed_role: UserRole,
) -> None:
    current_user = build_test_user(
        allowed_role
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    received_arguments: dict[
        str,
        object,
    ] = {}

    # 模拟 Service 完成更新并返回版本 2 的 ORM 对象。
    def fake_update_document(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        received_arguments.update(
            kwargs
        )

        document = build_knowledge_document(
            document_id="document-001",
            created_by="original-creator",
            title="新版密码说明",
            raw_content="员工验证邮箱后重置密码。",
            embedding_model="fake-update-model",
        )
        document.source_type = "manual"
        document.source_uri = None
        document.status = "ready"
        document.embedding_dimensions = (
            EMBEDDING_DIMENSIONS
        )
        document.version = 2
        document.created_at = TEST_TIMESTAMP
        document.updated_at = TEST_TIMESTAMP
        document.chunks.append(
            build_knowledge_chunk(
                chunk_id="new-chunk-001",
                content="员工验证邮箱后重置密码。",
            )
        )
        return document

    monkeypatch.setattr(
        knowledge_api,
        "update_document",
        fake_update_document,
    )

    response = client.put(
        "/v1/knowledge/documents/document-001",
        json={
            "title": "新版密码说明",
            "content": "员工验证邮箱后重置密码。",
            "expected_version": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "document-001"
    assert response.json()["version"] == 2
    assert response.json()["chunk_count"] == 1
    assert response.json()["created_by"] == (
        "original-creator"
    )

    # Router 必须把 HTTP 数据和依赖正确交给 Service。
    assert received_arguments["document_id"] == (
        "document-001"
    )
    assert received_arguments["expected_version"] == 1
    assert received_arguments["updated_by"] == (
        current_user.id
    )
    assert received_arguments["embedding_provider"] is (
        embedding_provider
    )
    assert isinstance(
        received_arguments["session"],
        Session,
    )

    raw_document = received_arguments[
        "raw_document"
    ]
    assert raw_document.title == "新版密码说明"
    assert raw_document.content == (
        "员工验证邮箱后重置密码。"
    )


# 这个测试负责验证更新不存在的知识文档会返回 404。
def test_update_knowledge_document_returns_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )
    monkeypatch.setattr(
        knowledge_api,
        "update_document",
        lambda **kwargs: None,
    )

    response = client.put(
        "/v1/knowledge/documents/missing-document",
        json={
            "title": "新版密码说明",
            "content": "员工验证邮箱后重置密码。",
            "expected_version": 1,
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "知识文档不存在",
    }


@pytest.mark.parametrize(
    (
        "error_type",
        "error_message",
    ),
    [
        pytest.param(
            knowledge_api.KnowledgeDocumentVersionConflictError,
            "知识文档版本冲突",
            id="stale-version",
        ),
        pytest.param(
            knowledge_api.InvalidKnowledgeDocumentUpdateStateError,
            "当前状态不允许更新",
            id="invalid-state",
        ),
        pytest.param(
            knowledge_api.KnowledgeDocumentNoChangesError,
            "知识文档内容没有变化",
            id="no-changes",
        ),
    ],
)
# 这个测试负责验证三种更新业务冲突都会返回 409。
def test_update_knowledge_document_returns_409_for_business_conflict(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[ValueError],
    error_message: str,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def failing_update_document(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        raise error_type(
            error_message
        )

    monkeypatch.setattr(
        knowledge_api,
        "update_document",
        failing_update_document,
    )

    response = client.put(
        "/v1/knowledge/documents/document-001",
        json={
            "title": "新版密码说明",
            "content": "员工验证邮箱后重置密码。",
            "expected_version": 1,
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
            {
                "title": "新版密码说明",
                "content": "员工验证邮箱后重置密码。",
            },
            id="missing-expected-version",
        ),
        pytest.param(
            {
                "title": "新版密码说明",
                "content": "员工验证邮箱后重置密码。",
                "expected_version": 0,
            },
            id="invalid-expected-version",
        ),
        pytest.param(
            {
                "title": "新版密码说明",
                "content": "   ",
                "expected_version": 1,
            },
            id="blank-content",
        ),
    ],
)
# 这个测试负责验证非法更新请求会在进入 Service 前返回 422。
def test_update_knowledge_document_rejects_invalid_request(
    monkeypatch: pytest.MonkeyPatch,
    invalid_body: dict[str, object],
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_update_document(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        raise AssertionError(
            "非法请求不应该进入更新 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "update_document",
        should_not_update_document,
    )

    response = client.put(
        "/v1/knowledge/documents/document-001",
        json=invalid_body,
    )

    assert response.status_code == 422
    assert "detail" in response.json()


# 这个测试负责验证过长文档 ID 会返回 422。
def test_update_knowledge_document_rejects_invalid_document_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_update_document(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        raise AssertionError(
            "非法文档 ID 不应该进入更新 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "update_document",
        should_not_update_document,
    )

    response = client.put(
        f"/v1/knowledge/documents/{'x' * 37}",
        json={
            "title": "新版密码说明",
            "content": "员工验证邮箱后重置密码。",
            "expected_version": 1,
        },
    )

    assert response.status_code == 422
    assert "detail" in response.json()


# 这个测试负责验证未登录用户不能更新知识文档。
def test_update_knowledge_document_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    app.dependency_overrides[
        get_session
    ] = fake_get_session
    app.dependency_overrides[
        get_embedding_provider
    ] = lambda: embedding_provider

    def should_not_update_document(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        raise AssertionError(
            "未登录用户不应该进入更新 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "update_document",
        should_not_update_document,
    )

    response = client.put(
        "/v1/knowledge/documents/document-001",
        json={
            "title": "新版密码说明",
            "content": "员工验证邮箱后重置密码。",
            "expected_version": 1,
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


# 这个测试负责验证普通客服不能更新知识文档。
def test_update_knowledge_document_forbids_agent_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "agent"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def should_not_update_document(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        raise AssertionError(
            "普通客服不应该进入更新 Service"
        )

    monkeypatch.setattr(
        knowledge_api,
        "update_document",
        should_not_update_document,
    )

    response = client.put(
        "/v1/knowledge/documents/document-001",
        json={
            "title": "新版密码说明",
            "content": "员工验证邮箱后重置密码。",
            "expected_version": 1,
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "权限不足",
    }


# 这个测试负责验证内部更新失败会返回安全的 HTTP 500。
def test_update_knowledge_document_returns_500_when_service_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    def failing_update_document(
        **kwargs,
    ) -> KnowledgeDocumentRecord:
        raise knowledge_api.KnowledgeDocumentUpdateError(
            "这里是不能泄漏给客户端的内部错误"
        )

    monkeypatch.setattr(
        knowledge_api,
        "update_document",
        failing_update_document,
    )

    response = client.put(
        "/v1/knowledge/documents/document-001",
        json={
            "title": "新版密码说明",
            "content": "员工验证邮箱后重置密码。",
            "expected_version": 1,
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }
    assert "不能泄漏" not in response.text


# 这个测试负责验证 PUT 接口会真正保存历史版本并替换片段。
def test_update_knowledge_document_persists_through_full_http_flow(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    database_session.commit()
    user_id = database_user.id

    document = build_knowledge_document(
        document_id="http-update-document",
        created_by=user_id,
        title="旧版密码说明",
        raw_content="员工使用旧流程重置密码。",
        embedding_model="old-embedding-model",
    )
    document.status = "ready"
    document.chunks.append(
        build_knowledge_chunk(
            chunk_id="http-old-chunk",
            content="员工使用旧流程重置密码。",
        )
    )
    database_session.add(
        document
    )
    database_session.commit()

    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )

    def fake_embed_texts(
        texts,
    ) -> EmbeddingBatch:
        return EmbeddingBatch(
            model_name="fake-update-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=tuple(
                tuple(
                    [0.6] * EMBEDDING_DIMENSIONS
                )
                for _ in texts
            ),
        )

    embedding_provider.embed_texts.side_effect = (
        fake_embed_texts
    )

    def use_database_session() -> Generator[
        Session,
        None,
        None,
    ]:
        yield database_session

    def use_database_user() -> UserRecord:
        return database_user

    def use_embedding_provider() -> (
        EmbeddingProvider
    ):
        return embedding_provider

    app.dependency_overrides[
        get_session
    ] = use_database_session
    app.dependency_overrides[
        get_current_user
    ] = use_database_user
    app.dependency_overrides[
        get_embedding_provider
    ] = use_embedding_provider

    response = client.put(
        "/v1/knowledge/documents/http-update-document",
        json={
            "title": "新版密码说明",
            "content": "员工验证企业邮箱后重置密码。",
            "expected_version": 1,
        },
    )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["version"] == 2
    assert response_data["title"] == "新版密码说明"
    assert response_data["chunk_count"] == 1

    database_session.expire_all()

    saved_document = database_session.get(
        KnowledgeDocumentRecord,
        "http-update-document",
    )
    saved_history = database_session.scalar(
        select(
            KnowledgeDocumentVersionRecord
        ).where(
            KnowledgeDocumentVersionRecord.document_id
            == "http-update-document"
        )
    )
    saved_chunks = database_session.scalars(
        select(
            KnowledgeChunkRecord
        ).where(
            KnowledgeChunkRecord.document_id
            == "http-update-document"
        )
    ).all()

    assert saved_document is not None
    assert saved_document.version == 2
    assert saved_document.updated_by == user_id
    assert saved_document.raw_content == (
        "员工验证企业邮箱后重置密码。"
    )
    assert saved_document.embedding_model == (
        "fake-update-model"
    )

    assert saved_history is not None
    assert saved_history.version == 1
    assert saved_history.title == "旧版密码说明"
    assert saved_history.superseded_by == user_id

    assert len(saved_chunks) == 1
    assert saved_chunks[0].id != "http-old-chunk"
    assert saved_chunks[0].content == (
        "员工验证企业邮箱后重置密码。"
    )
    embedding_provider.embed_texts.assert_called_once()


@pytest.mark.parametrize(
    "allowed_role",
    [
        pytest.param(
            "supervisor",
            id="supervisor",
        ),
        pytest.param(
            "admin",
            id="admin",
        ),
    ],
)
# 这个测试负责验证主管和管理员能够查询历史版本。
def test_list_knowledge_document_versions_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    allowed_role: UserRole,
) -> None:
    current_user = build_test_user(
        allowed_role
    )
    embedding_provider = MagicMock(
        spec=EmbeddingProvider
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=embedding_provider,
    )

    received_arguments: dict[
        str,
        object,
    ] = {}

    def fake_list_document_versions(
        session: Session,
        document_id: str,
        *,
        limit: int,
        offset: int,
    ) -> KnowledgeDocumentVersionListPage:
        received_arguments.update(
            {
                "session": session,
                "document_id": document_id,
                "limit": limit,
                "offset": offset,
            }
        )
        return KnowledgeDocumentVersionListPage(
            items=(
                KnowledgeDocumentVersionSummaryRow(
                    document_id=document_id,
                    version=2,
                    title="第二版密码说明",
                    source_type="manual",
                    source_uri=None,
                    embedding_model=(
                        "qwen3-embedding:0.6b"
                    ),
                    embedding_dimensions=(
                        EMBEDDING_DIMENSIONS
                    ),
                    cleaner_version="cleaner-v1",
                    chunker_version="chunker-v1",
                    created_by="creator-user",
                    created_at=TEST_TIMESTAMP,
                    superseded_by="updater-user",
                    superseded_at=TEST_TIMESTAMP,
                ),
            ),
            total=2,
            current_version=3,
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_document_versions",
        fake_list_document_versions,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions",
        params={
            "limit": 10,
            "offset": 1,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "document_id": "document-001",
        "current_version": 3,
        "items": [
            {
                "version": 2,
                "title": "第二版密码说明",
                "source_type": "manual",
                "source_uri": None,
                "embedding_model": (
                    "qwen3-embedding:0.6b"
                ),
                "embedding_dimensions": (
                    EMBEDDING_DIMENSIONS
                ),
                "cleaner_version": "cleaner-v1",
                "chunker_version": "chunker-v1",
                "created_by": "creator-user",
                "created_at": (
                    "2026-01-01T00:00:00Z"
                ),
                "superseded_by": "updater-user",
                "superseded_at": (
                    "2026-01-01T00:00:00Z"
                ),
            },
        ],
        "total": 2,
        "limit": 10,
        "offset": 1,
    }
    assert received_arguments["document_id"] == (
        "document-001"
    )
    assert received_arguments["limit"] == 10
    assert received_arguments["offset"] == 1
    assert isinstance(
        received_arguments["session"],
        Session,
    )


# 这个测试负责验证没有历史版本的文档会返回空页面。
def test_list_knowledge_document_versions_returns_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )
    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_document_versions",
        lambda *args, **kwargs: (
            KnowledgeDocumentVersionListPage(
                items=(),
                total=0,
                current_version=1,
            )
        ),
    )

    response = client.get(
        "/v1/knowledge/documents/new-document/versions"
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0
    assert response.json()["current_version"] == 1


# 这个测试负责验证文档不存在时返回 404。
def test_list_knowledge_document_versions_returns_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )
    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_document_versions",
        lambda *args, **kwargs: None,
    )

    response = client.get(
        "/v1/knowledge/documents/missing-document/versions"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "知识文档不存在",
    }


# 这个测试负责验证未登录用户不能查询历史版本。
def test_list_knowledge_document_versions_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    def should_not_list_versions(
        *args,
        **kwargs,
    ) -> KnowledgeDocumentVersionListPage:
        raise AssertionError(
            "未登录用户不应该查询历史版本"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_document_versions",
        should_not_list_versions,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions"
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


# 这个测试负责验证普通客服不能查询历史版本。
def test_list_knowledge_document_versions_forbids_agent_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "agent"
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    def should_not_list_versions(
        *args,
        **kwargs,
    ) -> KnowledgeDocumentVersionListPage:
        raise AssertionError(
            "普通客服不应该查询历史版本"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_document_versions",
        should_not_list_versions,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions"
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "权限不足",
    }


@pytest.mark.parametrize(
    "request_url",
    [
        pytest.param(
            "/v1/knowledge/documents/document-001/versions?limit=0",
            id="limit-too-small",
        ),
        pytest.param(
            "/v1/knowledge/documents/document-001/versions?limit=101",
            id="limit-too-large",
        ),
        pytest.param(
            "/v1/knowledge/documents/document-001/versions?offset=-1",
            id="negative-offset",
        ),
        pytest.param(
            f"/v1/knowledge/documents/{'x' * 37}/versions",
            id="document-id-too-long",
        ),
    ],
)
# 这个测试负责验证非法路径和分页参数返回 422。
def test_list_knowledge_document_versions_rejects_invalid_parameters(
    monkeypatch: pytest.MonkeyPatch,
    request_url: str,
) -> None:
    current_user = build_test_user(
        "supervisor"
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    def should_not_list_versions(
        *args,
        **kwargs,
    ) -> KnowledgeDocumentVersionListPage:
        raise AssertionError(
            "非法参数不应该进入 Repository"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_document_versions",
        should_not_list_versions,
    )

    response = client.get(
        request_url
    )

    assert response.status_code == 422
    assert "detail" in response.json()


# 这个测试负责验证数据库失败时返回安全的 HTTP 500。
def test_list_knowledge_document_versions_returns_500_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = build_test_user(
        "admin"
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    def failing_list_versions(
        *args,
        **kwargs,
    ) -> KnowledgeDocumentVersionListPage:
        raise KnowledgeRepositoryError(
            "这里是不能泄漏给客户端的内部错误"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "list_document_versions",
        failing_list_versions,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions"
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }
    assert "不能泄漏" not in response.text


# 这个测试负责验证 HTTP 接口会读取真实数据库历史版本。
def test_list_knowledge_document_versions_reads_real_database(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    database_session.commit()
    user_id = database_user.id

    document = build_knowledge_document(
        document_id="http-version-list-document",
        created_by=user_id,
        title="当前第 3 版",
    )
    document.version = 3
    database_session.add(
        document
    )
    database_session.flush()
    database_session.add_all(
        [
            build_knowledge_document_version(
                version_id="http-history-version-1",
                document_id=document.id,
                version=1,
                title="历史第 1 版",
                created_by=user_id,
                superseded_by=user_id,
            ),
            build_knowledge_document_version(
                version_id="http-history-version-2",
                document_id=document.id,
                version=2,
                title="历史第 2 版",
                created_by=user_id,
                superseded_by=user_id,
            ),
        ]
    )
    database_session.commit()

    def use_database_session() -> Generator[
        Session,
        None,
        None,
    ]:
        yield database_session

    def use_database_user() -> UserRecord:
        return database_user

    app.dependency_overrides[
        get_session
    ] = use_database_session
    app.dependency_overrides[
        get_current_user
    ] = use_database_user

    response = client.get(
        (
            "/v1/knowledge/documents/"
            "http-version-list-document/versions"
        )
    )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["document_id"] == (
        "http-version-list-document"
    )
    assert response_data["current_version"] == 3
    assert response_data["total"] == 2
    assert response_data["limit"] == 20
    assert response_data["offset"] == 0
    assert [
        item["version"]
        for item in response_data["items"]
    ] == [
        2,
        1,
    ]
    assert [
        item["title"]
        for item in response_data["items"]
    ] == [
        "历史第 2 版",
        "历史第 1 版",
    ]
    assert all(
        "raw_content" not in item
        for item in response_data["items"]
    )


@pytest.mark.parametrize(
    "allowed_role",
    [
        pytest.param("supervisor", id="supervisor"),
        pytest.param("admin", id="admin"),
    ],
)
# 这个测试负责验证主管和管理员能够查询历史版本详情。
def test_get_knowledge_document_version_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    allowed_role: UserRole,
) -> None:
    current_user = build_test_user(
        allowed_role
    )
    configure_dependencies(
        current_user=current_user,
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )
    received_arguments: dict[str, object] = {}

    def fake_get_document_version(
        session: Session,
        document_id: str,
        version: int,
    ) -> KnowledgeDocumentVersionDetailRow:
        received_arguments.update(
            {
                "session": session,
                "document_id": document_id,
                "version": version,
            }
        )
        return KnowledgeDocumentVersionDetailRow(
            document_id=document_id,
            version=version,
            title="第二版密码说明",
            source_type="manual",
            source_uri=None,
            embedding_model="qwen3-embedding:0.6b",
            embedding_dimensions=EMBEDDING_DIMENSIONS,
            cleaner_version="cleaner-v1",
            chunker_version="chunker-v1",
            created_by="creator-user",
            created_at=TEST_TIMESTAMP,
            superseded_by="updater-user",
            superseded_at=TEST_TIMESTAMP,
            raw_content="  用户最初提交的正文。  ",
            cleaned_content="用户最初提交的正文。",
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_version_by_number",
        fake_get_document_version,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions/2"
    )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["version"] == 2
    assert response_data["title"] == "第二版密码说明"
    assert response_data["raw_content"] == (
        "  用户最初提交的正文。  "
    )
    assert response_data["cleaned_content"] == (
        "用户最初提交的正文。"
    )
    assert "content_hash" not in response_data
    assert received_arguments["document_id"] == (
        "document-001"
    )
    assert received_arguments["version"] == 2
    assert isinstance(
        received_arguments["session"],
        Session,
    )


# 这个测试负责验证不存在的历史版本返回 404。
def test_get_knowledge_document_version_returns_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dependencies(
        current_user=build_test_user("admin"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )
    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_version_by_number",
        lambda *args, **kwargs: None,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions/99"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "知识文档历史版本不存在",
    }


# 这个测试负责验证未登录用户不能查询历史版本详情。
def test_get_knowledge_document_version_requires_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.dependency_overrides[
        get_session
    ] = fake_get_session

    def should_not_get_version(
        *args,
        **kwargs,
    ) -> KnowledgeDocumentVersionDetailRow:
        raise AssertionError(
            "未登录用户不应该查询历史版本详情"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_version_by_number",
        should_not_get_version,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions/1"
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }


# 这个测试负责验证普通客服不能查询历史版本详情。
def test_get_knowledge_document_version_forbids_agent_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dependencies(
        current_user=build_test_user("agent"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    def should_not_get_version(
        *args,
        **kwargs,
    ) -> KnowledgeDocumentVersionDetailRow:
        raise AssertionError(
            "普通客服不应该查询历史版本详情"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_version_by_number",
        should_not_get_version,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions/1"
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "权限不足",
    }


@pytest.mark.parametrize(
    "request_url",
    [
        pytest.param(
            "/v1/knowledge/documents/document-001/versions/0",
            id="version-too-small",
        ),
        pytest.param(
            "/v1/knowledge/documents/document-001/versions/not-number",
            id="version-not-integer",
        ),
        pytest.param(
            f"/v1/knowledge/documents/{'x' * 37}/versions/1",
            id="document-id-too-long",
        ),
    ],
)
# 这个测试负责验证非法路径参数会返回 422。
def test_get_knowledge_document_version_rejects_invalid_parameters(
    monkeypatch: pytest.MonkeyPatch,
    request_url: str,
) -> None:
    configure_dependencies(
        current_user=build_test_user("supervisor"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    def should_not_get_version(
        *args,
        **kwargs,
    ) -> KnowledgeDocumentVersionDetailRow:
        raise AssertionError(
            "非法路径参数不应该进入 Repository"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_version_by_number",
        should_not_get_version,
    )

    response = client.get(
        request_url
    )

    assert response.status_code == 422
    assert "detail" in response.json()


# 这个测试负责验证数据库异常会转换成安全的 HTTP 500。
def test_get_knowledge_document_version_returns_500_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dependencies(
        current_user=build_test_user("admin"),
        embedding_provider=MagicMock(
            spec=EmbeddingProvider
        ),
    )

    def failing_get_version(
        *args,
        **kwargs,
    ) -> KnowledgeDocumentVersionDetailRow:
        raise KnowledgeRepositoryError(
            "这里是不能泄漏给客户端的内部错误"
        )

    monkeypatch.setattr(
        knowledge_api.knowledge_repository,
        "get_document_version_by_number",
        failing_get_version,
    )

    response = client.get(
        "/v1/knowledge/documents/document-001/versions/1"
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "知识库服务暂时不可用",
    }
    assert "不能泄漏" not in response.text


# 这个测试负责验证详情接口会读取真实历史版本正文。
def test_get_knowledge_document_version_reads_real_database(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    database_session.commit()
    user_id = database_user.id

    document = build_knowledge_document(
        document_id="http-version-detail-document",
        created_by=user_id,
        title="当前第 2 版",
    )
    document.version = 2
    database_session.add(
        document
    )
    database_session.flush()
    database_session.add(
        build_knowledge_document_version(
            version_id="http-version-detail-1",
            document_id=document.id,
            version=1,
            title="历史第 1 版",
            raw_content="  历史原始正文。  ",
            cleaned_content="历史原始正文。",
            created_by=user_id,
            superseded_by=user_id,
        )
    )
    database_session.commit()

    def use_database_session() -> Generator[
        Session,
        None,
        None,
    ]:
        yield database_session

    def use_database_user() -> UserRecord:
        return database_user

    app.dependency_overrides[
        get_session
    ] = use_database_session
    app.dependency_overrides[
        get_current_user
    ] = use_database_user

    response = client.get(
        (
            "/v1/knowledge/documents/"
            "http-version-detail-document/versions/1"
        )
    )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["version"] == 1
    assert response_data["title"] == "历史第 1 版"
    assert response_data["raw_content"] == (
        "  历史原始正文。  "
    )
    assert response_data["cleaned_content"] == (
        "历史原始正文。"
    )
    assert "content_hash" not in response_data
