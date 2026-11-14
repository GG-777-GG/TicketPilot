"""验证 Worker 可以按提交顺序找到下一条等待入库的任务。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.persistence.job_repository import (
    find_next_queued_ingestion_job_id,
)
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个函数负责构造不同时间、状态的测试任务。
def build_job(
    *,
    job_id: str,
    requested_by: str,
    status: str,
    created_at: datetime,
) -> KnowledgeIngestionJobRecord:
    return KnowledgeIngestionJobRecord(
        id=job_id,
        title="后台任务测试",
        raw_content="员工忘记密码后联系 IT 服务台。",
        source_type="manual",
        visibility="internal",
        requested_by=requested_by,
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )


# 这个测试负责确认队列为空时返回 None。
def test_find_next_queued_job_returns_none_for_empty_queue(
    database_session: Session,
) -> None:
    assert find_next_queued_ingestion_job_id(database_session) is None


# 这个测试负责确认只找 queued，并按创建时间、ID 稳定排序。
def test_find_next_queued_job_orders_and_skips_other_statuses(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    ids = {
        "failed": "00000000-0000-0000-0000-000000000000",
        "later": "00000000-0000-0000-0000-000000000001",
        "first": "00000000-0000-0000-0000-000000000002",
        "second": "00000000-0000-0000-0000-000000000003",
        "running": "00000000-0000-0000-0000-000000000004",
    }
    jobs = [
        build_job(
            job_id=ids["failed"],
            requested_by=database_user.id,
            status="failed",
            created_at=start - timedelta(days=1),
        ),
        build_job(
            job_id=ids["later"],
            requested_by=database_user.id,
            status="queued",
            created_at=start + timedelta(days=1),
        ),
        build_job(
            job_id=ids["second"],
            requested_by=database_user.id,
            status="queued",
            created_at=start,
        ),
        build_job(
            job_id=ids["first"],
            requested_by=database_user.id,
            status="queued",
            created_at=start,
        ),
        build_job(
            job_id=ids["running"],
            requested_by=database_user.id,
            status="running",
            created_at=start - timedelta(days=1),
        ),
    ]
    database_session.add_all(jobs)
    database_session.flush()

    assert find_next_queued_ingestion_job_id(database_session) == ids["first"]

    # 查询本身不能领取任务或增加尝试次数。
    first = database_session.get(KnowledgeIngestionJobRecord, ids["first"])
    assert first is not None
    assert first.status == "queued"
    assert first.attempt_count == 0

    first.status = "running"
    database_session.flush()
    assert find_next_queued_ingestion_job_id(database_session) == ids["second"]

    second = database_session.get(KnowledgeIngestionJobRecord, ids["second"])
    assert second is not None
    second.status = "running"
    database_session.flush()
    assert find_next_queued_ingestion_job_id(database_session) == ids["later"]
