"""验证入库成功后，任务能正确关联已生成的知识文档。"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from tests.factories.knowledge import build_knowledge_document
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import (
    claim_queued_ingestion_job,
    mark_ingestion_job_succeeded,
)
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord

LEASE_TOKEN = "00000000-0000-0000-0000-000000000001"
WRONG_LEASE_TOKEN = "00000000-0000-0000-0000-000000000002"


# 这个测试负责确认只有运行中的任务能被标记为成功。
def test_running_job_can_be_marked_succeeded(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="入库成功说明",
            content="这篇文档已经完成入库。",
        ),
        requested_by=database_user.id,
    )
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id=job.id,
    )
    database_session.add(document)
    database_session.commit()

    assert claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ) is not None
    database_session.commit()

    completed = mark_ingestion_job_succeeded(
        database_session,
        job.id,
        document.id,
        lease_token=LEASE_TOKEN,
    )

    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.document_id == document.id
    assert completed.attempt_count == 1
    assert completed.last_error is None
    assert completed.lease_token is None
    assert completed.lease_expires_at is None

    # Repository 不提交；由调用它的 Service/Worker 控制事务。
    assert database_session.in_transaction() is True
    database_session.commit()
    database_session.expire_all()

    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "succeeded"
    assert saved.document_id == document.id
    assert saved.lease_token is None
    assert saved.lease_expires_at is None


# 这个测试负责确认旧 Worker 的 token 不能完成新 Worker 持有的任务。
def test_wrong_lease_token_cannot_mark_job_succeeded(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="租约隔离说明",
            content="只有当前租约持有者可以完成任务。",
        ),
        requested_by=database_user.id,
    )
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id=job.id,
    )
    database_session.add(document)
    database_session.commit()
    expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert claim_queued_ingestion_job(
        database_session,
        job.id,
        lease_token=LEASE_TOKEN,
        lease_expires_at=expires_at,
    ) is not None
    database_session.commit()

    assert mark_ingestion_job_succeeded(
        database_session,
        job.id,
        document.id,
        lease_token=WRONG_LEASE_TOKEN,
    ) is None
    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.document_id is None
    assert saved.lease_token == LEASE_TOKEN
    assert saved.lease_expires_at is not None

    assert mark_ingestion_job_succeeded(
        database_session,
        job.id,
        document.id,
        lease_token=LEASE_TOKEN,
    ) is not None


# 这个测试负责确认等待中的任务不能跳过处理而直接成功。
def test_queued_job_cannot_be_marked_succeeded(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="尚未处理说明",
            content="这篇文档还没有被领取。",
        ),
        requested_by=database_user.id,
    )
    document = build_knowledge_document(
        created_by=database_user.id,
        document_id=job.id,
    )
    database_session.add(document)
    database_session.commit()

    assert mark_ingestion_job_succeeded(
        database_session,
        job.id,
        document.id,
        lease_token=LEASE_TOKEN,
    ) is None

    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)
    assert saved is not None
    assert saved.status == "queued"
    assert saved.document_id is None
