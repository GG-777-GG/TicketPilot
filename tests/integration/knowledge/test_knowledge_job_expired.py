"""验证过期租约查询只生成待检查名单，不修改任务状态。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from tests.factories.knowledge import build_knowledge_document
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.persistence import job_repository
from ticketpilot.knowledge.persistence.job_repository import find_expired_ingestion_job_ids
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个函数负责构造不同状态和租约时间的测试任务。
def build_job(
    *,
    job_id: str,
    requested_by: str,
    status: str,
    lease_expires_at: datetime | None,
    created_at: datetime,
) -> KnowledgeIngestionJobRecord:
    return KnowledgeIngestionJobRecord(
        id=job_id,
        title="过期租约测试",
        raw_content="员工忘记密码后联系 IT 服务台。",
        source_type="manual",
        visibility="internal",
        requested_by=requested_by,
        status=status,
        attempt_count=1,
        lease_token=("test-token" if lease_expires_at is not None else None),
        lease_expires_at=lease_expires_at,
        created_at=created_at,
        updated_at=created_at,
    )


# 这个测试负责确认数据库没有过期任务时返回空元组。
def test_find_expired_jobs_returns_empty_tuple(
    database_session: Session,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)

    assert find_expired_ingestion_job_ids(database_session, now=now) == ()


# 这个测试负责确认到期边界、状态、空租约、排序和数量限制。
def test_find_expired_jobs_filters_orders_and_does_not_mutate(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    old_time = now - timedelta(hours=2)
    ids = {
        "first": "00000000-0000-0000-0000-000000000011",
        "second": "00000000-0000-0000-0000-000000000012",
        "exact": "00000000-0000-0000-0000-000000000013",
        "future": "00000000-0000-0000-0000-000000000014",
        "null": "00000000-0000-0000-0000-000000000015",
        "queued": "00000000-0000-0000-0000-000000000016",
        "failed": "00000000-0000-0000-0000-000000000017",
    }
    cases = [
        ("second", "running", old_time),
        ("first", "running", old_time),
        ("exact", "running", now),
        ("future", "running", now + timedelta(seconds=1)),
        ("null", "running", None),
        ("queued", "queued", old_time),
        ("failed", "failed", old_time),
    ]
    jobs = [
        build_job(
            job_id=ids[name],
            requested_by=database_user.id,
            status=status,
            lease_expires_at=expires_at,
            created_at=now - timedelta(days=1),
        )
        for name, status, expires_at in cases
    ]
    database_session.add_all(jobs)
    database_session.flush()

    assert find_expired_ingestion_job_ids(
        database_session, now=now, limit=2
    ) == (ids["first"], ids["second"])
    assert find_expired_ingestion_job_ids(
        database_session, now=now
    ) == (ids["first"], ids["second"], ids["exact"])

    # 查询只读：未修改状态、租约或尝试次数，也未提交事务。
    assert not database_session.dirty
    assert all(job.attempt_count == 1 for job in jobs)
    assert [job.status for job in jobs] == [status for _, status, _ in cases]
    assert [job.lease_token is None for job in jobs] == [
        expires_at is None for _, _, expires_at in cases
    ]
    assert database_session.in_transaction() is True


# 这个测试负责只读巡检过期任务是否已有同 ID 的知识文档。
def test_inspect_expired_jobs_reports_document_state_without_mutation(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    old_time = now - timedelta(hours=1)
    ids = {
        "missing": "00000000-0000-0000-0000-000000000021",
        "ready": "00000000-0000-0000-0000-000000000022",
        "archived": "00000000-0000-0000-0000-000000000023",
        "future": "00000000-0000-0000-0000-000000000024",
        "queued": "00000000-0000-0000-0000-000000000025",
    }
    jobs = [
        build_job(
            job_id=ids[name],
            requested_by=database_user.id,
            status=status,
            lease_expires_at=expires_at,
            created_at=old_time,
        )
        for name, status, expires_at in (
            ("ready", "running", old_time),
            ("missing", "running", old_time),
            ("archived", "running", now),
            ("future", "running", now + timedelta(seconds=1)),
            ("queued", "queued", old_time),
        )
    ]
    ready_document = build_knowledge_document(
        created_by=database_user.id,
        document_id=ids["ready"],
    )
    ready_document.status = "ready"
    archived_document = build_knowledge_document(
        created_by=database_user.id,
        document_id=ids["archived"],
    )
    archived_document.status = "archived"
    database_session.add_all([*jobs, ready_document, archived_document])
    database_session.flush()

    # running 任务尚未完成，document_id 仍是 NULL；只能按 job.id
    # 与 document.id 关联，不能按 job.document_id 关联。
    assert all(job.document_id is None for job in jobs)

    first_page = job_repository.inspect_expired_ingestion_jobs(
        database_session, now=now, limit=2
    )
    assert [
        (row.job_id, row.document_id, row.document_status)
        for row in first_page
    ] == [
        (ids["missing"], None, None),
        (ids["ready"], ids["ready"], "ready"),
    ]

    all_rows = job_repository.inspect_expired_ingestion_jobs(
        database_session, now=now
    )
    assert [
        (row.job_id, row.document_id, row.document_status)
        for row in all_rows
    ] == [
        (ids["missing"], None, None),
        (ids["ready"], ids["ready"], "ready"),
        (ids["archived"], ids["archived"], "archived"),
    ]
    assert not database_session.dirty
    assert [job.status for job in jobs] == [
        "running", "running", "running", "running", "queued"
    ]
