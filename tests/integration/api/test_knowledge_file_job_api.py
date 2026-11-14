"""异步文件上传 API 回归：临时数据库与目录，不连接真实模型。"""

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ticketpilot.app import app
from ticketpilot.core.database import get_session
from ticketpilot.identity.dependencies import get_current_user
from ticketpilot.knowledge.api import router as knowledge_api
from ticketpilot.knowledge.api.dependencies import (
    get_embedding_provider,
    get_knowledge_file_storage,
    get_knowledge_file_upload_service,
)
from ticketpilot.knowledge.application.jobs import KnowledgeJobSubmissionError
from ticketpilot.knowledge.application.uploads import KnowledgeFileUploadService
from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.embeddings.base import EmbeddingBatch, EmbeddingProvider
from ticketpilot.knowledge.persistence.file_storage import (
    KnowledgeFileStorageError,
    LocalKnowledgeFileStorage,
)
from ticketpilot.knowledge.persistence.models import KnowledgeDocumentRecord, KnowledgeIngestionJobRecord
from ticketpilot.knowledge.workers.worker import process_ingestion_job


URL = "/v1/knowledge/jobs/upload"
client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    """隔离本文件各题的依赖替换，结束后恢复正常依赖。"""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def upload_case(database_session, database_user, tmp_path):
    """提供真实业务 Service 和隔离的数据库、存储目录、已登录测试用户。"""
    database_session.commit()
    storage = LocalKnowledgeFileStorage(directory=tmp_path / "uploads")

    def use_session():
        """为本次 HTTP 请求提供测试专用 Session，避免连接开发数据库。"""
        yield database_session

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = lambda: database_user
    app.dependency_overrides[get_knowledge_file_storage] = lambda: storage
    # 较小限制便于测试超限行为，无需构造大文件。
    app.dependency_overrides[get_knowledge_file_upload_service] = lambda: KnowledgeFileUploadService(
        max_file_size_bytes=100
    )
    return database_user, storage


@pytest.mark.parametrize("role", ["admin", "supervisor"])
def test_upload_returns_queued_task_without_parsing(
    upload_case, database_session, monkeypatch, role,
):
    """授权用户提交 PDF 后立即获得 queued 任务，不触发解析、OCR 或向量依赖。"""
    user, storage = upload_case
    user.role = role
    database_session.commit()
    parse = MagicMock(side_effect=AssertionError("提交阶段不能解析文件"))
    monkeypatch.setattr(KnowledgeFileUploadService, "parse_upload", parse)
    embedding_dependency = MagicMock(side_effect=AssertionError("提交阶段不需要模型"))
    app.dependency_overrides[get_embedding_provider] = embedding_dependency
    content = b"not a valid PDF: worker will check later"
    response = client.post(
        URL, files={"file": ("contract.pdf", content, "application/pdf")},
        data={"title": "  维修合同  ", "visibility": "restricted"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["title"] == "维修合同"
    assert body["status"] == "queued"
    assert body["attempt_count"] == 0
    assert body["document_id"] is None
    assert body["requested_by"] == user.id
    assert body["visibility"] == "restricted"
    assert body["source_uri"] == "contract.pdf"
    assert not ({"storage_key", "raw_content", "lease_token"} & body.keys())
    job = database_session.get(KnowledgeIngestionJobRecord, body["id"])
    assert job.raw_content is None
    assert storage.read(storage_key=job.storage_key) == content
    assert database_session.scalar(select(func.count(KnowledgeDocumentRecord.id))) == 0
    assert client.get(f"/v1/knowledge/jobs/{job.id}").json()["status"] == "queued"
    parse.assert_not_called()
    embedding_dependency.assert_not_called()


@pytest.mark.parametrize(
    ("filename", "content", "form", "expected"),
    [
        ("guide.txt", b"", {"title": "Guide"}, 422),
        ("guide.txt", b"x" * 101, {"title": "Guide"}, 413),
        ("guide.doc", b"x", {"title": "Guide"}, 415),
        ("../guide.txt", b"x", {"title": "Guide"}, 422),
        ("guide.txt", b"x", {"title": " "}, 422),
        ("guide.txt", b"x", {}, 422),
        ("guide.txt", b"x", {"title": "Guide", "visibility": "public"}, 422),
    ],
)
def test_rejected_upload_creates_no_task_or_file(
    upload_case, database_session, tmp_path,
    filename,  # 包含正常文件名与非法路径的测试输入。
    content,  # 包含空文件、超限文件等输入。
    form,  # 标题与可见范围表单。
    expected,  # HTTP 413、415 或 422。
):
    """无效请求在文件保存和数据库任务提交前被拒绝。"""
    response = client.post(URL, files={"file": (filename, content, "application/octet-stream")}, data=form)
    assert response.status_code == expected, response.text
    assert not (tmp_path / "uploads").exists()
    assert database_session.scalar(select(func.count(KnowledgeIngestionJobRecord.id))) == 0


def test_agent_cannot_submit_file_job(upload_case, database_session, tmp_path):
    """普通员工不能通过新增的文件入口绕过知识管理权限。"""
    user, _ = upload_case
    user.role = "agent"
    database_session.commit()
    response = client.post(URL, files={"file": ("guide.txt", b"text")}, data={"title": "Guide"})
    assert response.status_code == 403
    assert not (tmp_path / "uploads").exists()
    assert database_session.scalar(select(func.count(KnowledgeIngestionJobRecord.id))) == 0


def test_file_job_requires_login(upload_case, tmp_path):
    """移除身份替换后，未携带 JWT 的请求得到 401。"""
    app.dependency_overrides.pop(get_current_user)
    response = client.post(URL, files={"file": ("guide.txt", b"text")}, data={"title": "Guide"})
    assert response.status_code == 401
    assert not (tmp_path / "uploads").exists()


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (KnowledgeFileStorageError("internal disk path"), "原始文件保存失败"),
        (KnowledgeJobSubmissionError("提交知识入库任务失败"), "提交知识入库任务失败"),
        (KnowledgeJobSubmissionError("任务 ID：test-id；提交状态待核实"), "任务 ID：test-id；提交状态待核实"),
    ],
)
def test_submission_failure_has_explicit_http_response(upload_case, monkeypatch, error, detail):
    """存储异常隐藏内部原因，提交状态不确定时保留业务提供的核实信息。"""
    monkeypatch.setattr(knowledge_api, "submit_file_ingestion_job", MagicMock(side_effect=error))
    response = client.post(URL, files={"file": ("guide.txt", b"text")}, data={"title": "Guide"})
    assert response.status_code == 500
    assert response.json()["detail"] == detail


def test_upload_then_worker_then_status_query(upload_case, database_session, monkeypatch, tmp_path):
    """串联真实 HTTP 提交、Worker 入库和 HTTP 状态查询，向量生成使用测试替身。"""
    from ticketpilot.core.config import settings

    monkeypatch.setattr(settings, "knowledge_upload_directory", tmp_path / "uploads")
    response = client.post(
        URL, files={"file": ("guide.txt", "维修部件保修三个月。".encode(), "text/plain")},
        data={"title": "维修说明"},
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    provider = MagicMock(spec=EmbeddingProvider)
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="test-embedding", dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.1,) * EMBEDDING_DIMENSIONS,),
    )
    result = process_ingestion_job(session=database_session, job_id=job_id, embedding_provider=provider)
    assert result.status == "succeeded"
    status_response = client.get(f"/v1/knowledge/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "succeeded"
    assert status_response.json()["document_id"] == job_id
    assert database_session.get(KnowledgeDocumentRecord, job_id).source_uri == "guide.txt"


def test_bounded_read_closes_upload_file(upload_case, database_session):
    """直接检查读取上限和文件关闭，避免整个大文件被读入内存。"""
    user, storage = upload_case
    stream = BytesIO(b"123456789")
    stream.read = MagicMock(wraps=stream.read)
    upload = UploadFile(filename="large.txt", file=stream)
    with pytest.raises(HTTPException) as caught:
        knowledge_api.submit_knowledge_file_job(
            session=database_session, current_user=user, storage=storage,
            upload_service=KnowledgeFileUploadService(max_file_size_bytes=4),
            file=upload, title="Guide", visibility="internal",
        )
    assert caught.value.status_code == 413
    stream.read.assert_called_once_with(5)
    assert stream.closed
