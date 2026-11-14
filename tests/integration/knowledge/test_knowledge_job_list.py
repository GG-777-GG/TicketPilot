"""验证任务列表的分页、提交人隔离和数据库异常转换。"""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.factories.identity import build_user
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.domain.documents import KnowledgeIngestionJobStatus, RawDocument
from ticketpilot.knowledge.persistence.job_repository import (
    KnowledgeJobRepositoryError,
    list_ingestion_jobs,
)


def test_list_jobs_pagination_and_owner_scope(database_session, database_user):
    """保存两个用户的任务，验证排序、分页和总数均遵守查询范围。"""
    other = build_user()
    database_session.add(other)
    database_session.flush()
    jobs = []
    for index, owner in enumerate([database_user, other, database_user]):
        job = submit_ingestion_job(
            session=database_session,
            raw_document=RawDocument(title=f"任务{index}", content="测试正文"),
            requested_by=owner.id,
        )
        job.created_at = datetime(2026, 1, index + 1, tzinfo=timezone.utc)
        jobs.append(job)
    database_session.commit()

    page = list_ingestion_jobs(database_session, requested_by=None, limit=1, offset=1)
    assert [job.id for job in page.items] == [jobs[1].id]
    assert page.total == 3

    page = list_ingestion_jobs(
        database_session, requested_by=database_user.id, limit=1, offset=1
    )
    assert [job.id for job in page.items] == [jobs[0].id]
    assert page.total == 2

    page = list_ingestion_jobs(database_session, requested_by=other.id)
    assert [job.id for job in page.items] == [jobs[1].id]
    assert page.total == 1

    page = list_ingestion_jobs(database_session, requested_by=database_user.id, offset=20)
    assert page.items == ()
    assert page.total == 2

    page = list_ingestion_jobs(database_session, requested_by="unknown-user")
    assert page.items == ()
    assert page.total == 0

    # 同一创建时间按 ID 排序，分页不依赖数据库的偶然返回顺序。
    for job in jobs:
        job.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    database_session.commit()
    page = list_ingestion_jobs(database_session, requested_by=None)
    assert [job.id for job in page.items] == sorted(job.id for job in jobs)


def test_empty_job_list(database_session):
    """空数据库返回空页和零总数。"""
    page = list_ingestion_jobs(database_session, requested_by=None)
    assert page.items == ()
    assert page.total == 0


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (20, -1)])
def test_invalid_pagination(limit, offset):
    """无效分页在访问数据库前被拒绝。"""
    session = Mock(spec=Session)
    with pytest.raises(ValueError):
        list_ingestion_jobs(session, requested_by=None, limit=limit, offset=offset)
    session.scalar.assert_not_called()
    session.scalars.assert_not_called()


@pytest.mark.parametrize("failed_method", ["scalar", "scalars"])
def test_query_error_is_wrapped(failed_method):
    """统计或列表查询失败均转换为 Repository 异常，不擅自提交事务。"""
    session = Mock(spec=Session)
    session.scalar.return_value = 1
    error = SQLAlchemyError("测试数据库错误")
    getattr(session, failed_method).side_effect = error
    with pytest.raises(KnowledgeJobRepositoryError) as caught:
        list_ingestion_jobs(session, requested_by=None)
    assert caught.value.__cause__ is error
    session.commit.assert_not_called()


@pytest.fixture
def status_case(database_session, database_user):
    """保存两位用户的混合状态任务，供筛选、计数和分页测试使用。"""
    other = build_user()
    database_session.add(other)
    database_session.flush()
    jobs = []
    cases = [
        (database_user, KnowledgeIngestionJobStatus.FAILED),
        (other, KnowledgeIngestionJobStatus.FAILED),
        (database_user, KnowledgeIngestionJobStatus.RUNNING),
        (database_user, KnowledgeIngestionJobStatus.FAILED),
        (database_user, KnowledgeIngestionJobStatus.SUCCEEDED),
        (database_user, KnowledgeIngestionJobStatus.QUEUED),
    ]
    for index, (owner, task_status) in enumerate(cases):
        job = submit_ingestion_job(
            session=database_session,
            raw_document=RawDocument(title=f"状态筛选任务{index}", content="测试正文"),
            requested_by=owner.id,
        )
        job.status = task_status.value
        job.created_at = datetime(2026, 1, index + 1, tzinfo=timezone.utc)
        jobs.append(job)
    database_session.commit()
    return jobs


@pytest.mark.parametrize(
    ("job_status", "expected_indices"),
    [
        (KnowledgeIngestionJobStatus.FAILED, [3, 0]),
        (KnowledgeIngestionJobStatus.RUNNING, [2]),
        (KnowledgeIngestionJobStatus.SUCCEEDED, [4]),
        (KnowledgeIngestionJobStatus.QUEUED, [5]),
        (None, [5, 4, 3, 2, 0]),
    ],
)
def test_status_filter_combines_with_owner(
    status_case, database_session, database_user, job_status, expected_indices,
):
    """各种状态与提交人同时生效；显式 None 仍返回本人所有状态的任务。"""
    page = list_ingestion_jobs(
        database_session, requested_by=database_user.id, job_status=job_status,
    )
    assert [job.id for job in page.items] == [status_case[i].id for i in expected_indices]
    assert page.total == len(expected_indices)


@pytest.mark.parametrize(("own_only", "expected_total", "expected_index"), [
    (False, 3, 1), (True, 2, 0),
])
def test_status_filter_applies_before_pagination(
    status_case, database_session, database_user, own_only, expected_total, expected_index,
):
    """管理员和主管均先限定状态与权限，再分页；总数不受当前页数量限制。"""
    page = list_ingestion_jobs(
        database_session,
        requested_by=database_user.id if own_only else None,
        job_status=KnowledgeIngestionJobStatus.FAILED,
        limit=1,
        offset=1,
    )
    assert [job.id for job in page.items] == [status_case[expected_index].id]
    assert page.total == expected_total


def test_status_filter_cannot_expose_other_users_count(status_case, database_session):
    """数据库中存在失败任务时，无本人任务的查询仍返回空页和零总数。"""
    page = list_ingestion_jobs(
        database_session,
        requested_by="user-with-no-jobs",
        job_status=KnowledgeIngestionJobStatus.FAILED,
    )
    assert page.items == ()
    assert page.total == 0
