"""验证 Service 重新排队失败任务时会正确提交事务。"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import (
    claim_queued_ingestion_job,
    mark_ingestion_job_failed,
)
from ticketpilot.knowledge.application.jobs import (
    retry_ingestion_job,
    submit_ingestion_job,
)
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个测试负责确认失败任务重新排队后，状态真正提交到数据库。
def test_retry_ingestion_job_commits_requeue(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="可重试说明",
            content="保存的原文供下一次处理。",
        ),
        requested_by=database_user.id,
    )
    assert claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token="00000000-0000-0000-0000-000000000001",
        lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ) is not None
    assert mark_ingestion_job_failed(
        database_session,
        job.id,
        "模型暂时不可用",
        lease_token="00000000-0000-0000-0000-000000000001",
    ) is not None
    database_session.commit()

    retried = retry_ingestion_job(
        session=database_session,
        job_id=job.id,
    )

    assert retried is not None
    assert retried.status == "queued"
    assert retried.attempt_count == 1
    assert retried.last_error == "模型暂时不可用"
    assert database_session.in_transaction() is False

    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "queued"


# 这个测试负责确认正常排队任务不被重复执行“重试”。
def test_retry_ingestion_job_skips_nonfailed_job(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="正常排队说明",
            content="尚未失败，不能重试。",
        ),
        requested_by=database_user.id,
    )

    assert retry_ingestion_job(
        session=database_session,
        job_id=job.id,
    ) is None
    assert database_session.in_transaction() is False

    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "queued"
    assert saved.attempt_count == 0
