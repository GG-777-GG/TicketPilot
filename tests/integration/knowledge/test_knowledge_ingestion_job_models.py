"""测试入库任务能持久保存重试所需的输入和处理结果。"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ticketpilot.core.database import Base
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个测试负责确认任务失败后仍能找到原文和错误，供下一次重试。
def test_ingestion_job_preserves_input_and_failure(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    assert "knowledge_ingestion_jobs" in Base.metadata.tables

    job = KnowledgeIngestionJobRecord(
        id=str(uuid4()),
        title="密码重置说明",
        raw_content="员工忘记密码后应联系 IT 服务台。",
        source_type="manual",
        source_uri=None,
        visibility="restricted",
        requested_by=database_user.id,
    )
    database_session.add(job)
    database_session.flush()

    assert job.status == "queued"
    assert job.attempt_count == 0
    assert job.document_id is None
    assert job.lease_token is None
    assert job.lease_expires_at is None

    job.status = "failed"
    job.attempt_count = 1
    job.last_error = "向量服务暂时不可用"
    database_session.commit()

    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)

    assert saved is not None
    assert saved.raw_content == "员工忘记密码后应联系 IT 服务台。"
    assert saved.visibility == "restricted"
    assert saved.requested_by == database_user.id
    assert saved.status == "failed"
    assert saved.attempt_count == 1
    assert saved.last_error == "向量服务暂时不可用"
    assert saved.lease_token is None
    assert saved.lease_expires_at is None
    assert saved.created_at is not None
    assert saved.updated_at is not None


# 这个测试负责确认一次领取的随机标识和到期时间能够持久保存。
def test_ingestion_job_persists_lease_fields(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    lease_token = str(uuid4())
    lease_expires_at = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
    job = KnowledgeIngestionJobRecord(
        id=str(uuid4()),
        title="租约测试文档",
        raw_content="验证入库任务租约字段。",
        source_type="manual",
        visibility="internal",
        requested_by=database_user.id,
        status="running",
        attempt_count=1,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
    )
    database_session.add(job)
    database_session.commit()
    database_session.expire_all()

    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)

    assert saved is not None
    assert saved.lease_token == lease_token
    assert saved.lease_expires_at is not None
    assert saved.lease_expires_at.replace(tzinfo=timezone.utc) == lease_expires_at
    assert KnowledgeIngestionJobRecord.__table__.c.lease_expires_at.type.timezone


# 这个测试负责验证数据库拒绝非法任务状态、来源和可见性。
@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("status", "unknown"),
        ("source_type", "unknown"),
        ("visibility", "external"),
        ("attempt_count", -1),
    ],
)
def test_ingestion_job_rejects_invalid_values(
    database_session: Session,
    database_user: UserRecord,
    field_name: str,
    invalid_value: str | int,
) -> None:
    job = KnowledgeIngestionJobRecord(
        id=str(uuid4()),
        title="测试文档",
        raw_content="测试重试任务的原始正文。",
        source_type="manual",
        visibility="internal",
        requested_by=database_user.id,
    )
    setattr(job, field_name, invalid_value)
    database_session.add(job)

    with pytest.raises(IntegrityError):
        database_session.flush()

    database_session.rollback()
