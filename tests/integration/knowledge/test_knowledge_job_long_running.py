"""验证长时间运行任务查询只生成待检查名单，不修改任务状态。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.persistence.job_repository import (
    find_long_running_ingestion_job_ids,
)
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个函数负责创建不同状态和更新时间的测试任务。
def build_job(
    *,
    job_id: str,
    requested_by: str,
    status: str,
    updated_at: datetime,
) -> KnowledgeIngestionJobRecord:
    return KnowledgeIngestionJobRecord(
        id=job_id,
        title="后台任务测试",
        raw_content="员工忘记密码后联系 IT 服务台。",
        source_type="manual",
        visibility="internal",
        requested_by=requested_by,
        status=status,
        attempt_count=1,
        created_at=updated_at,
        updated_at=updated_at,
    )


# 这个测试负责确认没有符合条件的任务时返回空元组。
def test_find_long_running_jobs_returns_empty_tuple(
    database_session: Session,
) -> None:
    cutoff = datetime(2026, 10, 14, tzinfo=timezone.utc)

    assert find_long_running_ingestion_job_ids(
        database_session,
        cutoff=cutoff,
    ) == ()


# 这个测试负责确认状态、严格时间边界、稳定排序和数量限制。
def test_find_long_running_jobs_filters_and_orders_without_mutation(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    cutoff = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    ids = {
        "first": "00000000-0000-0000-0000-000000000011",
        "second": "00000000-0000-0000-0000-000000000012",
        "third": "00000000-0000-0000-0000-000000000013",
        "exact": "00000000-0000-0000-0000-000000000014",
        "new": "00000000-0000-0000-0000-000000000015",
        "queued": "00000000-0000-0000-0000-000000000016",
        "failed": "00000000-0000-0000-0000-000000000017",
    }
    old_time = cutoff - timedelta(hours=2)
    jobs = [
        build_job(
            job_id=ids["second"],
            requested_by=database_user.id,
            status="running",
            updated_at=old_time,
        ),
        build_job(
            job_id=ids["first"],
            requested_by=database_user.id,
            status="running",
            updated_at=old_time,
        ),
        build_job(
            job_id=ids["third"],
            requested_by=database_user.id,
            status="running",
            updated_at=cutoff - timedelta(minutes=1),
        ),
        build_job(
            job_id=ids["exact"],
            requested_by=database_user.id,
            status="running",
            updated_at=cutoff,
        ),
        build_job(
            job_id=ids["new"],
            requested_by=database_user.id,
            status="running",
            updated_at=cutoff + timedelta(minutes=1),
        ),
        build_job(
            job_id=ids["queued"],
            requested_by=database_user.id,
            status="queued",
            updated_at=old_time,
        ),
        build_job(
            job_id=ids["failed"],
            requested_by=database_user.id,
            status="failed",
            updated_at=old_time,
        ),
    ]
    database_session.add_all(jobs)
    database_session.flush()

    assert find_long_running_ingestion_job_ids(
        database_session,
        cutoff=cutoff,
        limit=2,
    ) == (ids["first"], ids["second"])
    assert find_long_running_ingestion_job_ids(
        database_session,
        cutoff=cutoff,
    ) == (ids["first"], ids["second"], ids["third"])

    # 查询只读：不更新状态、尝试次数或 ORM 对象。
    assert not database_session.dirty
    assert all(job.attempt_count == 1 for job in jobs)
    assert [job.status for job in jobs] == [
        "running", "running", "running", "running", "running", "queued", "failed"
    ]
