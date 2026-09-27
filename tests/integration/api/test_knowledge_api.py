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
from sqlalchemy.orm import Session

from ticketpilot.knowledge.models import (
    KnowledgeDocumentRecord,
)

# 导入知识文档和片段测试工厂。
from tests.factories.knowledge import (
    build_knowledge_chunk,
    build_knowledge_document,
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
from ticketpilot.knowledge.constants import (
    EMBEDDING_DIMENSIONS,
)

# 导入真正的 Embedding Provider 依赖工厂。
from ticketpilot.knowledge.dependencies import (
    get_embedding_provider,
)

# 导入统一的 Embedding 接口。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
)

# 导入入库服务统一异常。
from ticketpilot.knowledge.ingestion import (
    KnowledgeIngestionError,
)

# 导入知识来源枚举。
from ticketpilot.knowledge.documents import (
    KnowledgeSourceType,
)

# 导入检索服务返回的内部结果类型。
from ticketpilot.knowledge.retrieval import (
    KnowledgeRetrievalError,
    RetrievedKnowledgeChunk,
)

# 导入知识库 Router 模块。
# 后面的测试会替换它实际调用的 ingest_document。
import ticketpilot.knowledge.router as knowledge_api


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
# 这个测试负责验证主管能够成功创建知识文档。
def test_create_knowledge_document_returns_201(
    monkeypatch,
    allowed_role: UserRole,
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
    response = client.post(
        "/v1/knowledge/documents",
        json={
            "title": "TicketPilot 密码重置说明",
            "content": "用户可以通过邮箱重置密码。",
        },
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

# 这个测试负责验证已登录用户能够成功检索知识。
def test_search_knowledge_returns_200(
    monkeypatch: pytest.MonkeyPatch,
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
                source_type=(
                    KnowledgeSourceType.MANUAL
                ),
                source_uri=None,
                chunk_index=0,
                content=(
                    "用户可以通过注册邮箱重置密码。"
                ),
                start_offset=0,
                end_offset=17,

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
                "source_type": "manual",
                "source_uri": None,
                "chunk_index": 0,
                "content": (
                    "用户可以通过注册邮箱重置密码。"
                ),
                "start_offset": 0,
                "end_offset": 17,
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