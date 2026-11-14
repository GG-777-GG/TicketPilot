"""任务列表接口回归：验证分页、角色权限和公开字段，使用内存数据库。"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.factories.identity import build_user
from ticketpilot.app import app
from ticketpilot.core.database import get_session
from ticketpilot.identity.dependencies import get_current_user
from ticketpilot.knowledge.api import router as knowledge_api
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import KnowledgeJobRepositoryError
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
    KnowledgeIngestionJobRecord,
)


URL = "/v1/knowledge/jobs"
client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    """隔离接口测试的身份和会话替换，测试后清理覆盖。"""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def list_case(database_session, database_user):
    """准备两位用户的三条任务，覆盖正文任务、文件任务和不同状态。"""
    other = build_user(role="supervisor")
    database_session.add(other)
    database_session.flush()
    jobs = []
    for index, owner in enumerate([database_user, other, database_user]):
        job = submit_ingestion_job(
            session=database_session,
            raw_document=RawDocument(title=f"分页任务{index}", content="内部任务正文"),
            requested_by=owner.id,
            visibility="restricted" if index == 1 else "internal",
        )
        job.created_at = datetime(2026, 1, index + 1, tzinfo=timezone.utc)
        jobs.append(job)

    # 文件任务包含实际非空的内部字段，确认响应不会泄露它们。
    file_job = jobs[1]
    file_job.source_type = "file"
    file_job.source_uri = "内部说明.pdf"
    file_job.raw_content = None
    file_job.storage_key = "a" * 32 + ".bin"
    file_job.original_filename = "内部说明.pdf"
    file_job.status = "running"
    file_job.attempt_count = 1
    file_job.lease_token = "00000000-0000-0000-0000-000000000001"
    file_job.lease_expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    jobs[2].status = "failed"
    jobs[2].attempt_count = 2
    jobs[2].last_error = "知识文件解析或 OCR 失败"
    database_session.commit()

    def use_session():
        """为 HTTP 请求提供独立测试数据库，避免访问开发数据库。"""
        yield database_session

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = lambda: database_user
    return jobs, other


def test_admin_sees_all_tasks_and_only_public_fields(list_case, database_session):
    """管理员读取所有提交人的任务，文件路径和租约等内部字段不被输出。"""
    jobs, _ = list_case
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {key: body[key] for key in ("total", "limit", "offset")} == {
        "total": 3, "limit": 20, "offset": 0,
    }
    assert [item["id"] for item in body["items"]] == [job.id for job in reversed(jobs)]
    public_fields = {
        "id", "title", "source_type", "source_uri", "visibility", "requested_by",
        "status", "attempt_count", "document_id", "last_error", "created_at", "updated_at",
        # 允许接口公开返回任务处理阶段。
        "processing_stage",
    }
    for item in body["items"]:
        assert set(item) == public_fields
    assert body["items"][0]["last_error"] == "知识文件解析或 OCR 失败"
    assert body["items"][1]["source_uri"] == "内部说明.pdf"
    assert body["items"][1]["status"] == "running"

    # 列表查询不处理任务，不创建文档，也不改变领取次数和租约。
    database_session.expire_all()
    assert [job.status for job in jobs] == ["queued", "running", "failed"]
    assert [job.attempt_count for job in jobs] == [0, 1, 2]
    assert jobs[1].lease_token == "00000000-0000-0000-0000-000000000001"
    assert database_session.scalar(select(func.count(KnowledgeIngestionJobRecord.id))) == 3
    assert database_session.scalar(select(func.count(KnowledgeDocumentRecord.id))) == 0


@pytest.mark.parametrize(
    ("role", "offset", "expected_total", "expected_index"),
    [("admin", 1, 3, 1), ("supervisor", 1, 2, 0), ("supervisor", 20, 2, None)],
)
def test_pagination_counts_only_accessible_tasks(
    list_case, database_user, role, offset, expected_total, expected_index,
):
    """先按身份限定数据，再分页；超出最后一页时仍返回正确总数。"""
    jobs, _ = list_case
    database_user.role = role
    response = client.get(URL, params={"limit": 1, "offset": offset})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == expected_total
    assert body["limit"] == 1
    assert body["offset"] == offset
    assert [item["id"] for item in body["items"]] == (
        [] if expected_index is None else [jobs[expected_index].id]
    )


@pytest.mark.parametrize("forged_scope", ["other", "empty"])
def test_supervisor_cannot_override_owner_via_query(list_case, database_user, forged_scope):
    """客户端伪造 requested_by 不能越过服务端确定的主管查询范围。"""
    jobs, other = list_case
    database_user.role = "supervisor"
    response = client.get(URL, params={
        "requested_by": other.id if forged_scope == "other" else "",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [jobs[2].id, jobs[0].id]
    assert all(item["requested_by"] == database_user.id for item in body["items"])


def test_supervisor_without_own_tasks_sees_empty_page(list_case):
    """系统中有其他人的任务时，没有自己任务的主管仍得到空列表和零总数。"""
    actor = build_user(role="supervisor")
    app.dependency_overrides[get_current_user] = lambda: actor
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "abc"}, {"offset": "abc"}],
)
def test_invalid_pagination_returns_422(list_case, monkeypatch, params):
    """FastAPI 在访问 Repository 前拒绝无效分页参数。"""
    query = MagicMock(side_effect=AssertionError("无效分页不应查询数据库"))
    monkeypatch.setattr(knowledge_api, "list_ingestion_jobs", query)
    response = client.get(URL, params=params)
    assert response.status_code == 422
    query.assert_not_called()


@pytest.mark.parametrize(("role", "expected"), [("agent", 403), (None, 401)])
def test_list_requires_management_role(list_case, database_user, monkeypatch, role, expected):
    """普通员工和未登录访问者不能查询任务列表，也不会执行列表数据库查询。"""
    if role is None:
        app.dependency_overrides.pop(get_current_user)
    else:
        database_user.role = role
    query = MagicMock(side_effect=AssertionError("未授权用户不能读取任务"))
    monkeypatch.setattr(knowledge_api, "list_ingestion_jobs", query)
    response = client.get(URL)
    assert response.status_code == expected
    query.assert_not_called()


def test_repository_error_returns_safe_500(list_case, monkeypatch):
    """数据库异常转换成统一中文错误，不把内部信息放进响应。"""
    monkeypatch.setattr(
        knowledge_api,
        "list_ingestion_jobs",
        MagicMock(side_effect=KnowledgeJobRepositoryError("private database details")),
    )
    response = client.get(URL)
    assert response.status_code == 500
    assert response.json() == {"detail": "查询知识入库任务列表失败"}


@pytest.mark.parametrize(
    ("role", "task_status", "expected_indices"),
    [
        ("admin", "queued", [0]),
        ("admin", "running", [1]),
        ("admin", "succeeded", [0]),
        ("admin", "failed", [2]),
        ("supervisor", "queued", [0]),
        ("supervisor", "running", []),
        ("supervisor", "succeeded", [0]),
        ("supervisor", "failed", [2]),
    ],
)
def test_http_status_filter_respects_role(
    list_case, database_session, database_user, role, task_status, expected_indices,
):
    """通过公开参数 status 查询四种状态，同时遵守主管只能查看本人任务的规则。"""
    jobs, _ = list_case
    database_user.role = role
    # 本题只验证列表对任务状态的筛选，不调用模型或 Worker。
    if task_status == "succeeded":
        jobs[0].status = "succeeded"
    database_session.commit()

    response = client.get(URL, params={"status": task_status})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [jobs[i].id for i in expected_indices]
    assert body["total"] == len(expected_indices)
    assert all(item["status"] == task_status for item in body["items"])


@pytest.mark.parametrize(
    ("role", "expected_total", "expected_indices"),
    [("admin", 2, [1]), ("supervisor", 1, [])],
)
def test_http_status_pagination_and_forged_scope(
    list_case, database_session, database_user, role, expected_total, expected_indices,
):
    """状态和权限先过滤、再分页；伪造提交人不扩大范围，total 只统计匹配任务。"""
    jobs, other = list_case
    database_user.role = role
    jobs[1].status = "failed"
    jobs[1].lease_token = None
    jobs[1].lease_expires_at = None
    database_session.commit()

    response = client.get(URL, params={
        "status": "failed", "limit": 1, "offset": 1, "requested_by": other.id,
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == expected_total
    assert body["limit"] == 1
    assert body["offset"] == 1
    assert [item["id"] for item in body["items"]] == [jobs[i].id for i in expected_indices]


@pytest.mark.parametrize("task_status", ["", "ready", "FAILED", "null", "unknown"])
def test_invalid_task_status_returns_422_before_query(list_case, monkeypatch, task_status):
    """非法状态在查询前被拒绝；空值和字符串 null 不等于省略可选参数。"""
    query = MagicMock(side_effect=AssertionError("非法任务状态不能访问 Repository"))
    monkeypatch.setattr(knowledge_api, "list_ingestion_jobs", query)

    response = client.get(URL, params={"status": task_status})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "status"]
    query.assert_not_called()
