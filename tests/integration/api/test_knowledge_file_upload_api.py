"""测试知识文件上传 API 的权限、解析与入库边界。"""

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.factories.docx import build_docx
from tests.factories.knowledge import build_knowledge_document
from tests.factories.pdf import build_pdf
from ticketpilot.app import app
from ticketpilot.core.database import get_session
from ticketpilot.identity.dependencies import get_current_user
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.api.dependencies import (
    get_embedding_provider,
    get_knowledge_file_upload_service,
)
from ticketpilot.knowledge.domain.documents import KnowledgeSourceType, RawDocument, SourcePage
from ticketpilot.knowledge.embeddings.base import EmbeddingBatch, EmbeddingProvider
from ticketpilot.knowledge.application.ingestion import KnowledgeIngestionError
from ticketpilot.knowledge.persistence.models import KnowledgeDocumentRecord
from ticketpilot.knowledge.application.uploads import KnowledgeFileUploadService
import ticketpilot.knowledge.api.router as knowledge_api


client = TestClient(app)
TEST_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
UPLOAD_URL = "/v1/knowledge/documents/upload"


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Generator[None, None, None]:
    """每项测试前后清除 FastAPI 依赖覆盖。"""

    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def build_user(role: str) -> UserRecord:
    """创建不需要持久化的测试用户。"""

    return UserRecord(
        id=f"{role}-upload-test-user",
        email=f"{role}-upload-test@example.com",
        display_name="上传测试用户",
        password_hash="not-used-in-test",
        role=role,
        is_active=True,
        created_at=TEST_TIMESTAMP,
    )


def configure_dependencies(role: str = "admin") -> UserRecord:
    """使上传接口使用测试用户、Session 和向量 Provider。"""

    user = build_user(role)

    def fake_get_session() -> Generator[Session, None, None]:
        with Session() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = fake_get_session
    app.dependency_overrides[get_embedding_provider] = (
        lambda: MagicMock(spec=EmbeddingProvider)
    )
    return user


def build_saved_document(
    *,
    raw_document: RawDocument,
    created_by: str,
    visibility: str = "internal",
):
    """构造已完成入库的文档，供 HTTP 响应序列化。"""

    document = build_knowledge_document(
        created_by=created_by,
        title=raw_document.title,
        raw_content=raw_document.content,
    )
    document.source_type = raw_document.source_type.value
    document.source_uri = raw_document.source_uri
    document.status = "ready"
    document.visibility = visibility
    document.embedding_dimensions = EMBEDDING_DIMENSIONS
    document.version = 1
    document.created_at = TEST_TIMESTAMP
    document.updated_at = TEST_TIMESTAMP
    return document


@pytest.mark.parametrize("role", ["supervisor", "admin"])
@pytest.mark.parametrize(
    ("requested_visibility", "expected_visibility"),
    [(None, "internal"), ("restricted", "restricted")],
)
def test_upload_txt_calls_existing_ingestion(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    requested_visibility: str | None,
    expected_visibility: str,
) -> None:
    """授权用户上传 TXT 后，入库服务收到正确的来源和正文。"""

    user = configure_dependencies(role)
    received: list[dict[str, object]] = []

    def fake_ingest_document(**kwargs):
        received.append(kwargs)
        return build_saved_document(
            raw_document=kwargs["raw_document"],
            created_by=kwargs["created_by"],
            visibility=kwargs["visibility"],
        )

    monkeypatch.setattr(knowledge_api, "ingest_document", fake_ingest_document)

    form_data = {"title": "密码指南"}
    if requested_visibility is not None:
        form_data["visibility"] = requested_visibility

    response = client.post(
        UPLOAD_URL,
        files={"file": ("guide.txt", "员工可以重置密码。".encode("utf-8"), "text/plain")},
        data=form_data,
    )

    assert response.status_code == 201
    assert len(received) == 1
    raw_document = received[0]["raw_document"]
    assert isinstance(raw_document, RawDocument)
    assert raw_document.title == "密码指南"
    assert raw_document.content == "员工可以重置密码。"
    assert raw_document.source_type is KnowledgeSourceType.FILE
    assert raw_document.source_uri == "guide.txt"
    assert raw_document.source_pages == ()
    assert received[0]["created_by"] == user.id
    assert received[0]["visibility"] == expected_visibility
    assert response.json()["source_type"] == "file"
    assert response.json()["visibility"] == expected_visibility


def test_upload_rejects_unknown_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上传接口不能把未知权限值当作 internal 悄悄保存。"""

    configure_dependencies()
    ingest_mock = MagicMock()
    monkeypatch.setattr(knowledge_api, "ingest_document", ingest_mock)

    response = client.post(
        UPLOAD_URL,
        files={"file": ("guide.txt", b"example", "text/plain")},
        data={"visibility": "public"},
    )

    assert response.status_code == 422
    ingest_mock.assert_not_called()


def test_upload_markdown_uses_document_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未提交标题时，Markdown 一级标题成为文档标题。"""

    configure_dependencies()
    received: list[RawDocument] = []

    def fake_ingest_document(**kwargs):
        raw_document = kwargs["raw_document"]
        received.append(raw_document)
        return build_saved_document(
            raw_document=raw_document,
            created_by=kwargs["created_by"],
        )

    monkeypatch.setattr(knowledge_api, "ingest_document", fake_ingest_document)

    response = client.post(
        UPLOAD_URL,
        files={"file": ("guide.md", "# 登录指南\n请联系管理员。".encode("utf-8"), "text/markdown")},
    )

    assert response.status_code == 201
    assert received[0].title == "登录指南"
    assert response.json()["title"] == "登录指南"


def test_upload_pdf_extracts_text_and_uses_filename_as_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF 上传经真实解析器提取文字，再交给现有入库流程。"""

    configure_dependencies()
    received: list[RawDocument] = []

    def fake_ingest_document(**kwargs):
        raw_document = kwargs["raw_document"]
        received.append(raw_document)
        return build_saved_document(
            raw_document=raw_document,
            created_by=kwargs["created_by"],
        )

    monkeypatch.setattr(knowledge_api, "ingest_document", fake_ingest_document)

    response = client.post(
        UPLOAD_URL,
        files={
            "file": (
                "password-guide.pdf",
                build_pdf("Reset password with administrator."),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 201
    assert received[0].title == "password-guide"
    assert received[0].content == "Reset password with administrator."
    assert received[0].source_type is KnowledgeSourceType.FILE
    assert received[0].source_uri == "password-guide.pdf"
    assert received[0].source_pages == (
        SourcePage(page_number=1, text="Reset password with administrator."),
    )
    assert response.json()["title"] == "password-guide"


def test_upload_scanned_pdf_uses_ocr_before_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描 PDF 经真实上传路径调用 OCR，再将识别文字交给入库流程。"""

    configure_dependencies()
    received: list[RawDocument] = []
    pdf_content = build_pdf("")

    class FakeOcr:
        def extract_pages(
            self,
            *,
            content: bytes,
            page_indices: tuple[int, ...],
        ) -> dict[int, str]:
            assert content == pdf_content
            assert page_indices == (0,)
            return {0: "扫描合同的保修期限为三个月。"}

    def fake_ingest_document(**kwargs):
        raw_document = kwargs["raw_document"]
        received.append(raw_document)
        return build_saved_document(
            raw_document=raw_document,
            created_by=kwargs["created_by"],
        )

    monkeypatch.setattr(
        "ticketpilot.knowledge.parsers.pdf.RapidOcrPdfPageExtractor",
        FakeOcr,
    )
    monkeypatch.setattr(knowledge_api, "ingest_document", fake_ingest_document)

    response = client.post(
        UPLOAD_URL,
        files={"file": ("scan.pdf", pdf_content, "application/pdf")},
    )

    assert response.status_code == 201
    assert len(received) == 1
    assert received[0].title == "scan"
    assert received[0].content == "扫描合同的保修期限为三个月。"
    assert received[0].source_uri == "scan.pdf"
    assert received[0].source_pages == (
        SourcePage(page_number=1, text="扫描合同的保修期限为三个月。"),
    )


def test_upload_docx_extracts_text_and_uses_filename_as_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DOCX 上传按段落与表格顺序提取文字后进入入库流程。"""

    configure_dependencies()
    received: list[RawDocument] = []

    def fake_ingest_document(**kwargs):
        raw_document = kwargs["raw_document"]
        received.append(raw_document)
        return build_saved_document(
            raw_document=raw_document,
            created_by=kwargs["created_by"],
        )

    monkeypatch.setattr(knowledge_api, "ingest_document", fake_ingest_document)

    response = client.post(
        UPLOAD_URL,
        files={
            "file": (
                "password-guide.docx",
                build_docx("Reset password.", [["Owner", "IT"]]),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 201
    assert received[0].title == "password-guide"
    assert received[0].content == "Reset password.\n\nOwner | IT"
    assert received[0].source_type is KnowledgeSourceType.FILE
    assert received[0].source_uri == "password-guide.docx"
    assert response.json()["title"] == "password-guide"


@pytest.mark.parametrize(
    ("filename", "content", "expected_status"),
    [
        ("guide.doc", b"unsupported", 415),
        ("guide.docx", b"not a DOCX archive", 422),
        ("guide.pdf", b"%PDF", 422),
        ("guide.txt", b"\xff", 422),
        ("empty.txt", b"", 422),
    ],
)
def test_upload_rejects_invalid_file(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    content: bytes,
    expected_status: int,
) -> None:
    """不支持的格式及无效内容不会进入入库流程。"""

    configure_dependencies()
    ingestion = MagicMock()
    monkeypatch.setattr(knowledge_api, "ingest_document", ingestion)

    response = client.post(
        UPLOAD_URL,
        files={"file": (filename, content, "application/octet-stream")},
    )

    assert response.status_code == expected_status
    ingestion.assert_not_called()


def test_upload_rejects_file_over_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超过限制时返回 413，而且不调用入库服务。"""

    configure_dependencies()
    app.dependency_overrides[get_knowledge_file_upload_service] = (
        lambda: KnowledgeFileUploadService(max_file_size_bytes=4)
    )
    ingestion = MagicMock()
    monkeypatch.setattr(knowledge_api, "ingest_document", ingestion)

    response = client.post(
        UPLOAD_URL,
        files={"file": ("large.txt", b"12345", "text/plain")},
    )

    assert response.status_code == 413
    ingestion.assert_not_called()


def test_upload_requires_authentication() -> None:
    """未登录用户不能上传文件。"""

    response = client.post(
        UPLOAD_URL,
        files={"file": ("guide.txt", b"knowledge", "text/plain")},
    )

    assert response.status_code == 401


def test_upload_forbids_agent_role() -> None:
    """普通 agent 用户没有知识文档上传权限。"""

    configure_dependencies("agent")

    response = client.post(
        UPLOAD_URL,
        files={"file": ("guide.txt", b"knowledge", "text/plain")},
    )

    assert response.status_code == 403


def test_upload_returns_500_when_ingestion_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入库失败时隐藏内部原因，只返回稳定的错误信息。"""

    configure_dependencies()

    def failing_ingest_document(**kwargs):
        raise KnowledgeIngestionError("内部数据库连接信息")

    monkeypatch.setattr(
        knowledge_api,
        "ingest_document",
        failing_ingest_document,
    )

    response = client.post(
        UPLOAD_URL,
        files={"file": ("guide.txt", b"knowledge", "text/plain")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "知识库服务暂时不可用"


def test_upload_persists_file_source_through_full_http_flow(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    """上传接口真正完成解析、清洗、向量生成及数据库提交。"""

    database_session.commit()
    provider = MagicMock(spec=EmbeddingProvider)

    def fake_embed_texts(texts) -> EmbeddingBatch:
        return EmbeddingBatch(
            model_name="fake-embedding-model",
            dimensions=EMBEDDING_DIMENSIONS,
            vectors=tuple(
                (0.1,) * EMBEDDING_DIMENSIONS
                for _ in texts
            ),
        )

    provider.embed_texts.side_effect = fake_embed_texts

    def use_database_session() -> Generator[Session, None, None]:
        yield database_session

    app.dependency_overrides[get_session] = use_database_session
    app.dependency_overrides[get_current_user] = lambda: database_user
    app.dependency_overrides[get_embedding_provider] = lambda: provider

    content = "# 员工密码重置\n" + "忘记密码请联系管理员。" * 100
    response = client.post(
        UPLOAD_URL,
        files={"file": ("password-guide.md", content.encode("utf-8"), "text/markdown")},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["source_type"] == "file"
    assert result["source_uri"] == "password-guide.md"
    assert result["created_by"] == database_user.id
    assert result["chunk_count"] >= 1

    database_session.expire_all()
    stored = database_session.get(KnowledgeDocumentRecord, result["id"])
    assert stored is not None
    assert stored.title == "员工密码重置"
    assert stored.raw_content == content
    assert stored.source_type == "file"
    assert stored.source_uri == "password-guide.md"
    assert len(stored.chunks) == result["chunk_count"]
