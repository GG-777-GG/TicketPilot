"""测试入库任务 Repository 只负责写入，不擅自结束事务。"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ticketpilot.knowledge.persistence.job_repository import (
    KnowledgeJobRepositoryError,
    add_ingestion_job,
    find_expired_ingestion_job_ids,
    find_long_running_ingestion_job_ids,
    find_next_queued_ingestion_job_id,
    inspect_expired_ingestion_jobs,
    requeue_expired_ingestion_job,
)
from ticketpilot.knowledge.persistence.models import KnowledgeIngestionJobRecord


# 这个函数负责构造待保存的测试任务。
def build_job() -> KnowledgeIngestionJobRecord:
    return KnowledgeIngestionJobRecord(
        id="test-job-id",
        title="密码重置说明",
        raw_content="员工忘记密码时联系 IT 服务台。",
        source_type="manual",
        visibility="internal",
        requested_by="test-user-id",
    )


# 这个测试负责验证 Repository 写入并 flush，但不提交事务。
def test_add_ingestion_job_flushes_without_committing() -> None:
    session = MagicMock(spec=Session)
    job = build_job()

    saved = add_ingestion_job(session, job)

    assert saved is job
    session.add.assert_called_once_with(job)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责验证数据库错误交给 Service 决定如何回滚。
def test_add_ingestion_job_wraps_database_error() -> None:
    session = MagicMock(spec=Session)
    session.flush.side_effect = SQLAlchemyError("database unavailable")
    job = build_job()

    with pytest.raises(KnowledgeJobRepositoryError):
        add_ingestion_job(session, job)

    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责确认查询失败时只转换异常，不擅自提交或回滚。
def test_find_next_queued_job_wraps_database_error() -> None:
    session = MagicMock(spec=Session)
    session.scalar.side_effect = SQLAlchemyError("database unavailable")

    with pytest.raises(KnowledgeJobRepositoryError):
        find_next_queued_ingestion_job_id(session)

    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责确认查询长时间运行任务的数据库错误会被统一转换。
def test_find_long_running_jobs_wraps_database_error() -> None:
    session = MagicMock(spec=Session)
    session.scalars.side_effect = SQLAlchemyError("database unavailable")
    cutoff = datetime(2026, 10, 14, tzinfo=timezone.utc)

    with pytest.raises(KnowledgeJobRepositoryError, match="查询长时间运行"):
        find_long_running_ingestion_job_ids(session, cutoff=cutoff)

    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责确认非法数量限制会在访问数据库前被拒绝。
@pytest.mark.parametrize("limit", [0, -1])
def test_find_long_running_jobs_rejects_nonpositive_limit(limit: int) -> None:
    session = MagicMock(spec=Session)
    cutoff = datetime(2026, 10, 14, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="limit 必须大于 0"):
        find_long_running_ingestion_job_ids(
            session,
            cutoff=cutoff,
            limit=limit,
        )

    session.scalars.assert_not_called()


# 这个测试负责确认过期租约查询返回 ID，且不擅自提交或回滚。
def test_find_expired_jobs_returns_ids_without_ending_transaction() -> None:
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = ["first-id", "second-id"]
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)

    assert find_expired_ingestion_job_ids(session, now=now) == (
        "first-id",
        "second-id",
    )
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责确认过期租约查询失败时只转换异常，不结束事务。
def test_find_expired_jobs_wraps_database_error() -> None:
    session = MagicMock(spec=Session)
    session.scalars.side_effect = SQLAlchemyError("database unavailable")
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)

    with pytest.raises(KnowledgeJobRepositoryError, match="租约过期"):
        find_expired_ingestion_job_ids(session, now=now)

    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责确认非法数量限制会在查询数据库前被拒绝。
@pytest.mark.parametrize("limit", [0, -1])
def test_find_expired_jobs_rejects_nonpositive_limit(limit: int) -> None:
    session = MagicMock(spec=Session)
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="limit 必须大于 0"):
        find_expired_ingestion_job_ids(session, now=now, limit=limit)

    session.scalars.assert_not_called()


# 这个测试负责确认巡检的非法数量限制不会访问数据库。
@pytest.mark.parametrize("limit", [0, -1])
def test_inspect_expired_jobs_rejects_nonpositive_limit(limit: int) -> None:
    session = MagicMock(spec=Session)
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="limit 必须大于 0"):
        inspect_expired_ingestion_jobs(session, now=now, limit=limit)

    session.execute.assert_not_called()


# 这个测试负责确认巡检查询失败时不擅自提交或回滚事务。
def test_inspect_expired_jobs_wraps_database_error() -> None:
    session = MagicMock(spec=Session)
    session.execute.side_effect = SQLAlchemyError("database unavailable")
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)

    with pytest.raises(KnowledgeJobRepositoryError, match="巡检租约过期"):
        inspect_expired_ingestion_jobs(session, now=now)

    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# 这个测试负责确认重新排队失败时由上层决定如何结束事务。
def test_requeue_expired_job_wraps_database_error() -> None:
    session = MagicMock(spec=Session)
    session.scalar.side_effect = SQLAlchemyError("database unavailable")
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)

    with pytest.raises(KnowledgeJobRepositoryError, match="重新排队租约过期"):
        requeue_expired_ingestion_job(
            session,
            "test-job-id",
            lease_token="test-lease-token",
            now=now,
        )

    session.commit.assert_not_called()
    session.rollback.assert_not_called()
