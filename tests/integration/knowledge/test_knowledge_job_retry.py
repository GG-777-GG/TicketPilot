"""验证失败的入库任务可以重新排队，并保留重试计数。"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import (
    claim_queued_ingestion_job,
    mark_ingestion_job_failed,
    requeue_failed_ingestion_job,
)
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个测试负责确认失败任务可以回到队列，下一次领取才增加次数。
def test_failed_job_can_be_requeued_and_claimed_again(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="重试说明",
            content="失败后继续用这篇原文重试。",
        ),
        requested_by=database_user.id,
    )
    first_token = "00000000-0000-0000-0000-000000000001"
    second_token = "00000000-0000-0000-0000-000000000002"
    expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token=first_token,
        lease_expires_at=expires_at,
    ) is not None
    assert mark_ingestion_job_failed(
        database_session,
        job.id,
        "向量服务暂时不可用",
        lease_token=first_token,
    ) is not None
    database_session.commit()

    requeued = requeue_failed_ingestion_job(database_session, job.id)

    assert requeued is not None
    assert requeued.status == "queued"
    assert requeued.attempt_count == 1
    # 等待期间保留上次失败原因，领取下一次任务时才清除。
    assert requeued.last_error == "向量服务暂时不可用"
    assert requeued.lease_token is None
    assert requeued.lease_expires_at is None
    assert requeued.raw_content == "失败后继续用这篇原文重试。"
    assert database_session.in_transaction() is True
    database_session.commit()

    claimed_again = claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token=second_token,
        lease_expires_at=expires_at,
    )
    assert claimed_again is not None
    assert claimed_again.status == "running"
    assert claimed_again.attempt_count == 2
    assert claimed_again.last_error is None
    assert claimed_again.lease_token == second_token
    database_session.commit()
    database_session.expire_all()

    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.attempt_count == 2
    assert saved.lease_token == second_token


# 这个测试负责确认未失败的任务不能被误当作重试任务。
def test_queued_job_cannot_be_requeued(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="尚未失败说明",
            content="仍然等待第一次处理。",
        ),
        requested_by=database_user.id,
    )

    assert requeue_failed_ingestion_job(
        database_session, job.id
    ) is None
    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "queued"
    assert saved.attempt_count == 0
