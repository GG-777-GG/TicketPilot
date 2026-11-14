"""验证 Service 领取任务后会结束事务，供后续处理使用。"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.application.jobs import (
    JOB_LEASE_DURATION,
    start_ingestion_job,
    submit_ingestion_job,
)
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个测试负责确认 Service 领取并提交任务状态。
def test_start_ingestion_job_commits_claim(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="后台处理说明",
            content="提交后等待后台领取。",
        ),
        requested_by=database_user.id,
    )

    before_claim = datetime.now(timezone.utc)
    started = start_ingestion_job(
        session=database_session,
        job_id=job.id,
    )
    after_claim = datetime.now(timezone.utc)

    assert started is not None
    assert started.status == "running"
    assert started.attempt_count == 1
    assert started.lease_token is not None
    UUID(started.lease_token)
    assert started.lease_expires_at is not None
    expires_at = started.lease_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    assert before_claim + JOB_LEASE_DURATION <= expires_at
    assert expires_at <= after_claim + JOB_LEASE_DURATION
    assert database_session.in_transaction() is False

    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.attempt_count == 1
    assert saved.lease_token == started.lease_token
    assert saved.lease_expires_at is not None


# 这个测试负责确认已被领取的任务不会开始第二次。
def test_start_ingestion_job_skips_already_running_job(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="不可重复领取说明",
            content="这篇文档只允许一个 Worker 处理。",
        ),
        requested_by=database_user.id,
    )

    first_claim = start_ingestion_job(
        session=database_session,
        job_id=job.id,
    )
    assert first_claim is not None
    first_token = first_claim.lease_token
    assert start_ingestion_job(
        session=database_session,
        job_id=job.id,
    ) is None
    assert database_session.in_transaction() is False

    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.attempt_count == 1
    assert saved.lease_token == first_token
