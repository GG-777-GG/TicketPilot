"""验证只有没有文档的过期租约任务才能原子地重新排队。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from tests.factories.knowledge import build_knowledge_document
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.persistence import job_repository
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import (
    claim_queued_ingestion_job,
    mark_ingestion_job_succeeded,
)
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个函数负责创建已领取、仍为 running 的测试任务。
def create_running_job(
    *,
    session: Session,
    requested_by: str,
    lease_token: str,
    lease_expires_at: datetime,
) -> str:
    job = submit_ingestion_job(
        session=session,
        raw_document=RawDocument(
            title="过期任务恢复测试",
            content="文档与任务状态必须一致。",
        ),
        requested_by=requested_by,
    )
    job_id = job.id
    assert claim_queued_ingestion_job(
        session,
        job_id,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
    ) is not None
    session.commit()
    return job_id


# 这个测试负责确认到期且没有文档的任务可以重新排队，次数暂不增加。
def test_expired_job_without_document_can_be_requeued(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    old_token = "00000000-0000-0000-0000-000000000031"
    job_id = create_running_job(
        session=database_session,
        requested_by=database_user.id,
        lease_token=old_token,
        lease_expires_at=now,
    )

    requeued = job_repository.requeue_expired_ingestion_job(
        database_session,
        job_id,
        lease_token=old_token,
        now=now,
    )

    assert requeued is not None
    assert requeued.status == "queued"
    assert requeued.attempt_count == 1
    assert requeued.lease_token is None
    assert requeued.lease_expires_at is None
    assert requeued.document_id is None
    # Repository 只修改当前事务，由上层决定提交。
    assert database_session.in_transaction() is True
    database_session.commit()

    # 旧 Worker 的 token 已不能把重新排队的任务标记成功。
    assert mark_ingestion_job_succeeded(
        database_session,
        job_id,
        job_id,
        lease_token=old_token,
    ) is None
    database_session.rollback()

    next_token = "00000000-0000-0000-0000-000000000032"
    claimed_again = claim_queued_ingestion_job(
        database_session,
        job_id,
        lease_token=next_token,
        lease_expires_at=now + timedelta(hours=1),
    )
    assert claimed_again is not None
    assert claimed_again.status == "running"
    assert claimed_again.attempt_count == 2
    assert claimed_again.lease_token == next_token


# 这个测试负责确认旧 token 不能重新排队其他 Worker 持有的任务。
def test_expired_job_requeue_rejects_wrong_token(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    current_token = "00000000-0000-0000-0000-000000000033"
    job_id = create_running_job(
        session=database_session,
        requested_by=database_user.id,
        lease_token=current_token,
        lease_expires_at=now - timedelta(seconds=1),
    )

    assert job_repository.requeue_expired_ingestion_job(
        database_session,
        job_id,
        lease_token="00000000-0000-0000-0000-000000000034",
        now=now,
    ) is None
    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job_id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.lease_token == current_token
    assert saved.attempt_count == 1


# 这个测试负责确认未到期任务不能被提前重新排队。
def test_unexpired_job_cannot_be_requeued(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    token = "00000000-0000-0000-0000-000000000035"
    job_id = create_running_job(
        session=database_session,
        requested_by=database_user.id,
        lease_token=token,
        lease_expires_at=now + timedelta(seconds=1),
    )

    assert job_repository.requeue_expired_ingestion_job(
        database_session,
        job_id,
        lease_token=token,
        now=now,
    ) is None
    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job_id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.lease_token == token


# 这个测试负责确认已有文档的过期任务必须留给后续核对，不得重跑。
def test_expired_job_with_document_cannot_be_requeued(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    token = "00000000-0000-0000-0000-000000000036"
    job_id = create_running_job(
        session=database_session,
        requested_by=database_user.id,
        lease_token=token,
        lease_expires_at=now - timedelta(seconds=1),
    )
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id=job_id,
    )
    document.status = "ready"
    database_session.add(document)
    database_session.commit()

    assert job_repository.requeue_expired_ingestion_job(
        database_session,
        job_id,
        lease_token=token,
        now=now,
    ) is None
    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job_id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.lease_token == token
    assert saved.document_id is None
