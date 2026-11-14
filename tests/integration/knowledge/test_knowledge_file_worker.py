"""文件 Worker 回归：使用临时文件、内存数据库和模拟向量服务。"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from ticketpilot.core.config import settings
from ticketpilot.knowledge.application.jobs import (
    retry_ingestion_job,
    submit_file_ingestion_job,
)
from ticketpilot.knowledge.application.uploads import KnowledgeFileUploadService
from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.embeddings.base import EmbeddingBatch, EmbeddingProvider, EmbeddingProviderError
from ticketpilot.knowledge.parsers.base import KnowledgeFileParsingError
from ticketpilot.knowledge.persistence.file_storage import LocalKnowledgeFileStorage
from ticketpilot.knowledge.persistence.models import KnowledgeChunkRecord, KnowledgeDocumentRecord
from ticketpilot.knowledge.workers.worker import process_ingestion_job


@pytest.fixture
def file_worker_case(database_session, database_user, tmp_path, monkeypatch):
    """创建固定文本文件任务并配置临时存储；不连接真实模型或磁盘上传目录。"""
    monkeypatch.setattr(settings, "knowledge_upload_directory", tmp_path)
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    job = submit_file_ingestion_job(
        session=database_session,
        storage=storage,
        upload_service=KnowledgeFileUploadService(),
        title="文件任务测试",
        filename="guide.txt",
        content_type="text/plain",
        content="保修从送货日起计算，非人为故障保修三个月。".encode(),
        requested_by=database_user.id,
        visibility="restricted",
    )
    provider = MagicMock(spec=EmbeddingProvider)
    provider.embed_texts.return_value = EmbeddingBatch(
        model_name="test-embedding", dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.1,) * EMBEDDING_DIMENSIONS,),
    )
    return job, storage, provider


def test_file_worker_persists_document_and_keeps_task_input(database_session, file_worker_case):
    """真实读取和解析 TXT 后保存文档，任务继续保留文件标识而不混入正文。"""
    job, storage, provider = file_worker_case
    result = process_ingestion_job(session=database_session, job_id=job.id, embedding_provider=provider)
    assert result.status == "succeeded"
    assert result.document_id == job.id
    assert result.raw_content is None
    assert result.attempt_count == 1
    assert result.lease_token is None
    document = database_session.get(KnowledgeDocumentRecord, job.id)
    assert document is not None
    assert document.title == job.title
    assert document.visibility == "restricted"
    assert document.source_uri == "guide.txt"
    assert "送货日" in document.raw_content
    assert storage.read(storage_key=result.storage_key)


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("missing", "原始文件读取失败"),
        ("damaged-pdf", "知识文件解析或 OCR 失败"),
        ("ocr", "知识文件解析或 OCR 失败"),
        ("empty", "入库任务输入或文件校验失败"),
        ("unsupported", "知识文件类型不受支持"),
    ],
)
def test_file_input_failure_is_persisted(
    database_session, file_worker_case, tmp_path, monkeypatch,
    failure,  # 模拟的文件读取、校验或解析错误。
    expected_error,  # 对外任务记录应保存的简明错误类别。
):
    """输入处理失败后必须提交 failed，释放租约，且不调用向量模型。"""
    job, storage, provider = file_worker_case
    job_id, key = job.id, job.storage_key
    if failure == "missing":
        storage.delete(storage_key=key)
    elif failure == "damaged-pdf":
        # 原有 TXT 内容伪装为 PDF 名称，实际 PDF 解析会失败。
        job.original_filename = "damaged.pdf"
        database_session.commit()
    elif failure == "ocr":
        # 模拟 OCR 底层错误已被解析器转换为统一的解析异常。
        monkeypatch.setattr(
            KnowledgeFileUploadService, "parse_upload",
            MagicMock(side_effect=KnowledgeFileParsingError("OCR unavailable")),
        )
    elif failure == "empty":
        (tmp_path / key).write_bytes(b"")
    elif failure == "unsupported":
        job.original_filename = "legacy.doc"
        database_session.commit()

    result = process_ingestion_job(session=database_session, job_id=job_id, embedding_provider=provider)
    assert result.status == "failed"
    assert result.last_error == expected_error
    assert result.document_id is None
    assert result.lease_token is None
    assert result.lease_expires_at is None
    assert result.attempt_count == 1
    assert not database_session.in_transaction()
    provider.embed_texts.assert_not_called()
    assert database_session.get(KnowledgeDocumentRecord, job_id) is None
    if failure != "missing":
        assert (tmp_path / key).exists()


def test_file_job_can_retry_after_embedding_failure(database_session, file_worker_case):
    """向量服务恢复后用原文件重试，最终只生成一份文档和一组片段。"""
    job, storage, provider = file_worker_case
    job_id, key = job.id, job.storage_key
    provider.embed_texts.side_effect = EmbeddingProviderError("temporarily unavailable")
    failed = process_ingestion_job(session=database_session, job_id=job_id, embedding_provider=provider)
    assert failed.status == "failed"
    assert failed.last_error == "知识文档入库失败"
    assert storage.read(storage_key=key)
    assert retry_ingestion_job(session=database_session, job_id=job_id).status == "queued"
    provider.embed_texts.side_effect = None
    completed = process_ingestion_job(session=database_session, job_id=job_id, embedding_provider=provider)
    assert completed.status == "succeeded"
    assert completed.attempt_count == 2
    assert completed.last_error is None
    assert completed.raw_content is None
    assert completed.storage_key == key
    assert database_session.scalar(select(func.count(KnowledgeDocumentRecord.id))) == 1
    assert database_session.scalar(select(func.count(KnowledgeChunkRecord.id))) == 1
