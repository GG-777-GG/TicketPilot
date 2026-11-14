"""验证入库任务只能从 queued 状态被领取一次。"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import claim_queued_ingestion_job
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个测试负责确认一次领取会改为 running 并增加处理次数。
def test_claim_queued_job_only_once(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="待处理说明",
            content="这篇文档需要后台处理。",
        ),
        requested_by=database_user.id,
    )

    expires_at = datetime(2099, 1, 1, 12, 30, tzinfo=timezone.utc)
    second_expires_at = datetime(2099, 1, 2, 12, 30, tzinfo=timezone.utc)
    first_token = "00000000-0000-0000-0000-000000000001"
    second_token = "00000000-0000-0000-0000-000000000002"
    claimed = claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token=first_token,
        lease_expires_at=expires_at,
    )
    claimed_again = claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token=second_token,
        lease_expires_at=second_expires_at,
    )

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    assert claimed.lease_token == first_token
    assert claimed.lease_expires_at is not None
    assert claimed_again is None

    # Repository 不提交；Worker 稍后决定事务如何结束。
    assert database_session.in_transaction() is True
    database_session.commit()
    database_session.expire_all()

    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.attempt_count == 1
    assert saved.lease_token == first_token
    assert saved.lease_expires_at is not None
    assert saved.lease_expires_at.replace(tzinfo=timezone.utc) == expires_at


# 这个测试负责确认不存在的任务不会被凭空创建。
def test_claim_missing_job_returns_none(
    database_session: Session,
) -> None:
    assert claim_queued_ingestion_job(
        database_session,
        "missing-job-id",
        lease_token="00000000-0000-0000-0000-000000000001",
        lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ) is None
