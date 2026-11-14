"""验证运行中的入库任务失败后，错误信息能够持久保存。"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import (
    claim_queued_ingestion_job,
    mark_ingestion_job_failed,
)
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord

LEASE_TOKEN = "00000000-0000-0000-0000-000000000001"
WRONG_LEASE_TOKEN = "00000000-0000-0000-0000-000000000002"


# 这个测试负责确认失败任务保留原文、失败原因和尝试次数。
def test_running_job_can_be_marked_failed(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="向量生成失败说明",
            content="失败后仍需保存这段原文，供以后重试。",
        ),
        requested_by=database_user.id,
    )
    assert claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ) is not None
    database_session.commit()

    failed = mark_ingestion_job_failed(
        database_session,
        job.id,
        "向量服务暂时不可用",
        lease_token=LEASE_TOKEN,
    )

    assert failed is not None
    assert failed.status == "failed"
    assert failed.last_error == "向量服务暂时不可用"
    assert failed.attempt_count == 1
    assert failed.document_id is None
    assert failed.lease_token is None
    assert failed.lease_expires_at is None

    # Repository 不提交，调用方负责提交或回滚。
    assert database_session.in_transaction() is True
    database_session.commit()
    database_session.expire_all()

    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "failed"
    assert saved.last_error == "向量服务暂时不可用"
    assert saved.raw_content == "失败后仍需保存这段原文，供以后重试。"
    assert saved.attempt_count == 1
    assert saved.lease_token is None
    assert saved.lease_expires_at is None


# 这个测试负责确认错误 token 不能把别的 Worker 的任务标记失败。
def test_wrong_lease_token_cannot_mark_job_failed(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="失败租约隔离说明",
            content="旧 Worker 不得改写新 Worker 的任务状态。",
        ),
        requested_by=database_user.id,
    )
    assert claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ) is not None
    database_session.commit()

    assert mark_ingestion_job_failed(
        database_session,
        job.id,
        "旧 Worker 的错误",
        lease_token=WRONG_LEASE_TOKEN,
    ) is None
    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.last_error is None
    assert saved.lease_token == LEASE_TOKEN
    assert saved.lease_expires_at is not None

    assert mark_ingestion_job_failed(
        database_session,
        job.id,
        "当前 Worker 的错误",
        lease_token=LEASE_TOKEN,
    ) is not None


# 这个测试负责确认未领取的任务不能跳过处理而直接失败。
def test_queued_job_cannot_be_marked_failed(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="尚未领取说明",
            content="这篇文档还在等待处理。",
        ),
        requested_by=database_user.id,
    )

    assert mark_ingestion_job_failed(
        database_session,
        job.id,
        "不应写入的错误",
        lease_token=LEASE_TOKEN,
    ) is None

    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "queued"
    assert saved.last_error is None
