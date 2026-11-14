"""验证 Worker 把持久化任务交给现有入库流水线。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.constants import EMBEDDING_DIMENSIONS
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingBatch,
    EmbeddingProvider,
    EmbeddingProviderError,
)
from ticketpilot.knowledge.application.jobs import (
    retry_ingestion_job,
    submit_ingestion_job,
)
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
    KnowledgeIngestionJobRecord,
)
from ticketpilot.knowledge.workers import worker as knowledge_worker
from ticketpilot.knowledge.workers.worker import (
    KnowledgeJobProcessingError,
    process_ingestion_job,
    process_next_ingestion_job,
)


# 这个测试负责确认自动处理一次只取最早任务，剩余任务下次再处理。
def test_process_next_ingestion_job_processes_one_at_a_time(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    first = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="先提交的文档",
            content="第一篇文档的正文。",
        ),
        requested_by=database_user.id,
    )
    second = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="后提交的文档",
            content="第二篇文档的正文。",
        ),
        requested_by=database_user.id,
    )
    first.created_at = datetime(2026, 10, 12, tzinfo=timezone.utc)
    second.created_at = first.created_at + timedelta(seconds=1)
    database_session.commit()

    embedding_provider = MagicMock(spec=EmbeddingProvider)
    embedding_provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-embedding-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.1,) * EMBEDDING_DIMENSIONS,),
    )

    first_result = process_next_ingestion_job(
        session=database_session,
        embedding_provider=embedding_provider,
    )
    assert first_result is not None
    assert first_result.id == first.id
    assert first_result.status == "succeeded"
    database_session.expire_all()
    remaining = database_session.get(KnowledgeIngestionJobRecord, second.id)
    assert remaining is not None
    assert remaining.status == "queued"

    second_result = process_next_ingestion_job(
        session=database_session,
        embedding_provider=embedding_provider,
    )
    assert second_result is not None
    assert second_result.id == second.id
    assert second_result.status == "succeeded"
    assert process_next_ingestion_job(
        session=database_session,
        embedding_provider=embedding_provider,
    ) is None
    assert embedding_provider.embed_texts.call_count == 2


# 这个测试负责确认任务完成后，文档与任务都保存到数据库。
def test_process_ingestion_job_succeeds(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="后台入库说明",
            content="员工忘记密码后应联系 IT 服务台。",
        ),
        requested_by=database_user.id,
        visibility="restricted",
    )
    embedding_provider = MagicMock(spec=EmbeddingProvider)
    embedding_provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-embedding-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.1,) * EMBEDDING_DIMENSIONS,),
    )

    completed = process_ingestion_job(
        session=database_session,
        job_id=job.id,
        embedding_provider=embedding_provider,
    )

    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.document_id == job.id
    assert completed.attempt_count == 1
    assert completed.last_error is None
    assert completed.lease_token is None
    assert completed.lease_expires_at is None
    assert database_session.in_transaction() is False
    embedding_provider.embed_texts.assert_called_once()

    database_session.expire_all()
    saved_job = database_session.get(KnowledgeIngestionJobRecord, job.id)
    document = database_session.get(KnowledgeDocumentRecord, job.id)
    assert saved_job is not None
    assert document is not None
    assert saved_job.status == "succeeded"
    assert document.title == "后台入库说明"
    assert document.visibility == "restricted"
    assert document.created_by == database_user.id


# 这个测试负责确认向量服务失败时任务保留原文和失败状态。
def test_process_ingestion_job_records_failure(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="失败后待重试说明",
            content="原文必须保留，以便以后重试。",
        ),
        requested_by=database_user.id,
    )
    embedding_provider = MagicMock(spec=EmbeddingProvider)
    embedding_provider.embed_texts.side_effect = EmbeddingProviderError(
        "Ollama 暂时不可用"
    )

    failed = process_ingestion_job(
        session=database_session,
        job_id=job.id,
        embedding_provider=embedding_provider,
    )

    assert failed is not None
    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.last_error is not None
    assert failed.document_id is None
    assert failed.lease_token is None
    assert failed.lease_expires_at is None
    assert database_session.in_transaction() is False

    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.raw_content == "原文必须保留，以便以后重试。"
    assert database_session.get(KnowledgeDocumentRecord, job.id) is None


# 这个测试负责确认成功的任务不会被再次处理。
def test_process_ingestion_job_skips_finished_job(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="不重复入库说明",
            content="相同任务只执行一次。",
        ),
        requested_by=database_user.id,
    )
    embedding_provider = MagicMock(spec=EmbeddingProvider)
    embedding_provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-embedding-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.1,) * EMBEDDING_DIMENSIONS,),
    )

    assert process_ingestion_job(
        session=database_session,
        job_id=job.id,
        embedding_provider=embedding_provider,
    ) is not None
    assert process_ingestion_job(
        session=database_session,
        job_id=job.id,
        embedding_provider=embedding_provider,
    ) is None
    embedding_provider.embed_texts.assert_called_once()


# 这个测试负责确认第一次失败后可以用相同任务和原文重试成功。
def test_failed_ingestion_job_can_succeed_on_retry(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="重试成功说明",
            content="第一次模型不可用，第二次恢复后应完成入库。",
        ),
        requested_by=database_user.id,
    )
    embedding_provider = MagicMock(spec=EmbeddingProvider)
    embedding_provider.embed_texts.side_effect = EmbeddingProviderError(
        "模型暂时不可用"
    )

    first_result = process_ingestion_job(
        session=database_session,
        job_id=job.id,
        embedding_provider=embedding_provider,
    )
    assert first_result is not None
    assert first_result.status == "failed"

    requeued = retry_ingestion_job(
        session=database_session,
        job_id=job.id,
    )
    assert requeued is not None
    assert requeued.status == "queued"

    embedding_provider.embed_texts.side_effect = None
    embedding_provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-embedding-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.2,) * EMBEDDING_DIMENSIONS,),
    )
    second_result = process_ingestion_job(
        session=database_session,
        job_id=job.id,
        embedding_provider=embedding_provider,
    )

    assert second_result is not None
    assert second_result.status == "succeeded"
    assert second_result.attempt_count == 2
    assert second_result.document_id == job.id
    assert second_result.last_error is None

    database_session.expire_all()
    document = database_session.get(KnowledgeDocumentRecord, job.id)
    assert document is not None
    assert document.title == "重试成功说明"
    assert embedding_provider.embed_texts.call_count == 2


# 这个测试负责确认任务状态无法完成时，文档写入也会一起回滚。
def test_worker_rolls_back_document_when_completion_loses_lease(
    database_session: Session,
    database_user: UserRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="提交边界测试",
            content="文档与任务结果必须一起提交。",
        ),
        requested_by=database_user.id,
    )
    job_id = job.id
    embedding_provider = MagicMock(spec=EmbeddingProvider)
    embedding_provider.embed_texts.return_value = EmbeddingBatch(
        model_name="fake-embedding-model",
        dimensions=EMBEDDING_DIMENSIONS,
        vectors=((0.1,) * EMBEDDING_DIMENSIONS,),
    )
    # 模拟租约被其他 Worker 接手：最终任务状态更新没有匹配到当前 token。
    monkeypatch.setattr(
        knowledge_worker,
        "mark_ingestion_job_succeeded",
        MagicMock(return_value=None),
    )

    with pytest.raises(KnowledgeJobProcessingError, match="无法更新任务状态"):
        process_ingestion_job(
            session=database_session,
            job_id=job_id,
            embedding_provider=embedding_provider,
        )

    database_session.expire_all()
    saved_job = database_session.get(KnowledgeIngestionJobRecord, job_id)
    assert saved_job is not None
    assert saved_job.status == "running"
    # 只领取状态被独立提交；文档和最终成功状态属于同一个可回滚事务。
    assert database_session.get(KnowledgeDocumentRecord, job_id) is None
