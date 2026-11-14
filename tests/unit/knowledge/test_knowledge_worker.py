"""测试 Worker 从持久化任务恢复入库所需的原始文档。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from ticketpilot.knowledge.domain.documents import KnowledgeSourceType
from ticketpilot.knowledge.embeddings.base import EmbeddingProvider
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord
from ticketpilot.knowledge.workers import worker
from ticketpilot.knowledge.workers.worker import build_raw_document_from_job


# 这个测试负责确认 Worker 不会丢失文件来源和原始正文。
def test_build_raw_document_from_job_preserves_source() -> None:
    job = KnowledgeIngestionJobRecord(
        id="test-job-id",
        title="员工操作手册",
        raw_content="上传文件解析后保存的原文。",
        source_type="file",
        source_uri="employee-guide.pdf",
        visibility="restricted",
        requested_by="test-user-id",
    )

    raw_document = build_raw_document_from_job(job)

    assert raw_document.title == "员工操作手册"
    assert raw_document.content == "上传文件解析后保存的原文。"
    assert raw_document.source_type is KnowledgeSourceType.FILE
    assert raw_document.source_uri == "employee-guide.pdf"


# 这个测试负责确认空队列不会调用入库流程。
def test_process_next_ingestion_job_returns_none_for_empty_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    provider = MagicMock(spec=EmbeddingProvider)
    process = MagicMock()
    monkeypatch.setattr(
        worker,
        "find_next_queued_ingestion_job_id",
        lambda _session: None,
    )
    monkeypatch.setattr(worker, "process_ingestion_job", process)

    result = worker.process_next_ingestion_job(
        session=session,
        embedding_provider=provider,
    )

    assert result is None
    process.assert_not_called()


# 这个测试负责确认找到任务后交给已有的原子领取流程。
def test_process_next_ingestion_job_delegates_to_existing_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    provider = MagicMock(spec=EmbeddingProvider)
    completed = object()
    process = MagicMock(return_value=completed)
    monkeypatch.setattr(
        worker,
        "find_next_queued_ingestion_job_id",
        lambda _session: "next-job-id",
    )
    monkeypatch.setattr(worker, "process_ingestion_job", process)

    result = worker.process_next_ingestion_job(
        session=session,
        embedding_provider=provider,
    )

    assert result is completed
    process.assert_called_once_with(
        session=session,
        job_id="next-job-id",
        embedding_provider=provider,
    )


# 这个测试负责确认候选任务被其他 Worker 抢先领取时安全返回 None。
def test_process_next_ingestion_job_handles_lost_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    provider = MagicMock(spec=EmbeddingProvider)
    monkeypatch.setattr(
        worker,
        "find_next_queued_ingestion_job_id",
        lambda _session: "claimed-by-another-worker",
    )
    process = MagicMock(return_value=None)
    monkeypatch.setattr(worker, "process_ingestion_job", process)

    assert worker.process_next_ingestion_job(
        session=session,
        embedding_provider=provider,
    ) is None
    process.assert_called_once()


# 这个测试负责确认 Worker 使用领取时的 token，而不是入库后可能变化的 token。
def test_process_ingestion_job_keeps_claimed_lease_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    provider = MagicMock(spec=EmbeddingProvider)
    job = KnowledgeIngestionJobRecord(
        id="test-job-id",
        title="租约测试",
        raw_content="文档内容",
        source_type="manual",
        source_uri=None,
        visibility="internal",
        requested_by="test-user-id",
        lease_token="claimed-token",
    )
    monkeypatch.setattr(worker, "start_ingestion_job", lambda **_: job)

    def finish_ingestion(**_: object) -> SimpleNamespace:
        # 模拟入库过程中 ORM 对象重新加载，显示了别的 Worker 的 token。
        job.lease_token = "later-token"
        return SimpleNamespace(id="document-id")

    monkeypatch.setattr(worker, "ingest_document", finish_ingestion)
    completed = object()
    mark_succeeded = MagicMock(return_value=completed)
    monkeypatch.setattr(worker, "mark_ingestion_job_succeeded", mark_succeeded)

    result = worker.process_ingestion_job(
        session=session,
        job_id=job.id,
        embedding_provider=provider,
    )

    assert result is completed
    mark_succeeded.assert_called_once_with(
        session,
        job.id,
        "document-id",
        lease_token="claimed-token",
    )
