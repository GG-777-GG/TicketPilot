"""重试接口回归：内存数据库和临时文件，无真实模型或开发数据。"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.factories.identity import build_user
from ticketpilot.app import app
from ticketpilot.core.config import settings
from ticketpilot.core.database import get_session
from ticketpilot.identity.dependencies import get_current_user
from ticketpilot.knowledge.api import router as knowledge_api
from ticketpilot.knowledge.application.jobs import KnowledgeJobRetryError, submit_file_ingestion_job
from ticketpilot.knowledge.application.uploads import KnowledgeFileUploadService
from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.embeddings.base import EmbeddingBatch, EmbeddingProvider, EmbeddingProviderError
from ticketpilot.knowledge.persistence.file_storage import LocalKnowledgeFileStorage
from ticketpilot.knowledge.persistence.job_repository import KnowledgeJobRepositoryError
from ticketpilot.knowledge.persistence.models import KnowledgeDocumentRecord, KnowledgeIngestionJobRecord
from ticketpilot.knowledge.workers.worker import process_ingestion_job


client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    """清理每道题前后的 FastAPI 测试依赖，避免污染其他接口测试。"""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def retry_case(database_session, database_user, tmp_path):
    """保存带原始文件的失败任务；HTTP 身份默认是任务提交人。"""
    database_session.commit()
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    job = submit_file_ingestion_job(
        session=database_session,
        storage=storage,
        upload_service=KnowledgeFileUploadService(),
        title="失败重试测试",
        filename="guide.txt",
        content_type="text/plain",
        content="保修从送货日起计算。".encode(),
        requested_by=database_user.id,
        visibility="restricted",
    )
    job.status = "failed"
    job.attempt_count = 2
    job.last_error = "知识文档入库失败"
    database_session.commit()

    def use_session():
        """向 HTTP 请求提供本题独享的内存数据库会话。"""
        yield database_session

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = lambda: database_user
    return job, storage


@pytest.mark.parametrize(
    ("role", "own_task", "expected"),
    [("admin", True, 202), ("admin", False, 202),
     ("supervisor", True, 202), ("supervisor", False, 404),
     ("agent", True, 403)],
)
def test_retry_respects_role_and_ownership(
    retry_case, database_session, database_user, role, own_task, expected,
):
    """权限允许时只重新排队，不创建任务，不改变文件或尝试次数。"""
    job, storage = retry_case
    key = job.storage_key
    original = storage.read(storage_key=key)
    actor = database_user if own_task else build_user()
    actor.role = role
    if not own_task:
        database_session.add(actor)
    database_session.commit()
    app.dependency_overrides[get_current_user] = lambda: actor
    response = client.post(f"/v1/knowledge/jobs/{job.id}/retry")
    assert response.status_code == expected, response.text
    database_session.refresh(job)
    assert job.status == ("queued" if expected == 202 else "failed")
    assert job.attempt_count == 2
    assert job.storage_key == key
    assert job.raw_content is None
    assert storage.read(storage_key=key) == original
    assert database_session.scalar(select(func.count(KnowledgeIngestionJobRecord.id))) == 1
    if expected == 202:
        body = response.json()
        assert body["id"] == job.id
        assert body["status"] == "queued"
        assert body["attempt_count"] == 2
        assert not ({"storage_key", "lease_token", "raw_content"} & body.keys())
        # 同一任务已经排队，再次点击重试应冲突，不重复入队。
        assert client.post(f"/v1/knowledge/jobs/{job.id}/retry").status_code == 409


@pytest.mark.parametrize("task_status", ["queued", "running", "succeeded"])
def test_retry_rejects_non_failed_state(retry_case, database_session, task_status):
    """只有 failed 可以重试，其他状态不能被重新排队。"""
    job, _ = retry_case
    job.status = task_status
    database_session.commit()
    response = client.post(f"/v1/knowledge/jobs/{job.id}/retry")
    assert response.status_code == 409
    assert response.json()["detail"] == "任务当前状态不允许重试"
    database_session.refresh(job)
    assert job.status == task_status
    assert job.attempt_count == 2


def test_retry_missing_task_returns_404(retry_case):
    """不存在的任务与不可访问的其他人任务都使用 404。"""
    assert client.post("/v1/knowledge/jobs/missing/retry").status_code == 404


def test_retry_requires_login(retry_case):
    """未提供 JWT 时不能进入任务重试流程。"""
    job, _ = retry_case
    app.dependency_overrides.pop(get_current_user)
    assert client.post(f"/v1/knowledge/jobs/{job.id}/retry").status_code == 401


@pytest.mark.parametrize(
    ("method", "error", "detail"),
    [
        ("get_ingestion_job", KnowledgeJobRepositoryError("private details"), "查询知识入库任务失败"),
        ("retry_ingestion_job", KnowledgeJobRetryError("private details"), "重试知识入库任务失败"),
    ],
)
def test_retry_internal_failure_returns_safe_error(retry_case, monkeypatch, method, error, detail):
    """查询和重新排队的内部异常转换为稳定的 HTTP 500。"""
    job, _ = retry_case
    monkeypatch.setattr(knowledge_api, method, MagicMock(side_effect=error))
    response = client.post(f"/v1/knowledge/jobs/{job.id}/retry")
    assert response.status_code == 500
    assert response.json()["detail"] == detail


def test_state_change_between_lookup_and_update_returns_409(retry_case, monkeypatch):
    """模拟预查询后更新未匹配，接口必须报告冲突而非返回旧任务。"""
    job, _ = retry_case
    monkeypatch.setattr(knowledge_api, "retry_ingestion_job", lambda **kwargs: None)
    assert client.post(f"/v1/knowledge/jobs/{job.id}/retry").status_code == 409


def test_http_retry_allows_worker_to_finish_existing_file_task(
    retry_case, database_session, tmp_path, monkeypatch,
):
    """串联真实失败、HTTP 重试、Worker 成功；文件复用且不生成重复文档。"""
    job, storage = retry_case
    job.status = "queued"
    job.attempt_count = 0
    database_session.commit()
    job_id, key = job.id, job.storage_key
    monkeypatch.setattr(settings, "knowledge_upload_directory", tmp_path)
    provider = MagicMock(spec=EmbeddingProvider)
    provider.embed_texts.side_effect = EmbeddingProviderError("temporary outage")
    failed = process_ingestion_job(session=database_session, job_id=job_id, embedding_provider=provider)
    assert failed.status == "failed"
    assert failed.attempt_count == 1

    response = client.post(f"/v1/knowledge/jobs/{job_id}/retry")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["attempt_count"] == 1
    assert storage.read(storage_key=key)

    provider.embed_texts.side_effect = None
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="test-embedding", dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.1,) * EMBEDDING_DIMENSIONS,),
    )
    completed = process_ingestion_job(session=database_session, job_id=job_id, embedding_provider=provider)
    assert completed.status == "succeeded"
    assert completed.document_id == job_id
    assert completed.attempt_count == 2
    assert completed.last_error is None
    assert database_session.scalar(select(func.count(KnowledgeDocumentRecord.id))) == 1
    assert database_session.scalar(select(func.count(KnowledgeIngestionJobRecord.id))) == 1
