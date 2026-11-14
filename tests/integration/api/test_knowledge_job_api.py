"""测试知识入库任务 API 的提交、权限和异步响应边界。"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ticketpilot.app import app
from ticketpilot.core.database import get_session
from ticketpilot.identity.dependencies import get_current_user
from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
    KnowledgeIngestionJobRecord,
)


client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Generator[None, None, None]:
    """确保每个 API 测试不会沿用其他测试的依赖覆盖。"""

    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# 这个测试负责确认提交只保存任务，不同步生成知识文档。
def test_submit_knowledge_job_returns_202_without_ingesting(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    database_session.commit()

    def use_session() -> Generator[Session, None, None]:
        yield database_session

    def use_user() -> UserRecord:
        return database_user

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = use_user

    response = client.post(
        "/v1/knowledge/jobs",
        json={
            "title": "后台入库测试",
            "content": "这段原文先排队，不在 HTTP 请求里生成向量。",
            "visibility": "restricted",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["title"] == "后台入库测试"
    assert body["status"] == "queued"
    assert body["visibility"] == "restricted"
    assert body["attempt_count"] == 0
    assert body["document_id"] is None
    assert body["requested_by"] == database_user.id
    assert "raw_content" not in body

    saved_job = database_session.get(KnowledgeIngestionJobRecord, body["id"])
    assert saved_job is not None
    assert saved_job.raw_content == (
        "这段原文先排队，不在 HTTP 请求里生成向量。"
    )
    assert database_session.get(KnowledgeDocumentRecord, body["id"]) is None


# 这个测试负责确认普通 agent 不能提交知识库入库任务。
def test_submit_knowledge_job_forbids_agent(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    database_user.role = "agent"
    database_session.commit()

    def use_session() -> Generator[Session, None, None]:
        yield database_session

    def use_user() -> UserRecord:
        return database_user

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = use_user

    response = client.post(
        "/v1/knowledge/jobs",
        json={
            "title": "权限不足说明",
            "content": "普通员工不能直接管理企业知识库。",
        },
    )

    assert response.status_code == 403


# 这个测试负责确认提交者可以查询自己任务的当前状态。
def test_get_knowledge_job_returns_own_status(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="待查询说明",
            content="提交后客户端可以轮询任务状态。",
        ),
        requested_by=database_user.id,
    )

    def use_session() -> Generator[Session, None, None]:
        yield database_session

    def use_user() -> UserRecord:
        return database_user

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = use_user

    response = client.get(f"/v1/knowledge/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json()["id"] == job.id
    assert response.json()["status"] == "queued"
    assert "raw_content" not in response.json()


# 这个测试负责确认不存在的任务返回 404。
def test_get_knowledge_job_returns_404_for_missing_id(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    database_session.commit()

    def use_session() -> Generator[Session, None, None]:
        yield database_session

    def use_user() -> UserRecord:
        return database_user

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = use_user

    response = client.get("/v1/knowledge/jobs/missing-job-id")

    assert response.status_code == 404


# 这个测试负责确认主管不能查看其他人的任务。
def test_get_knowledge_job_hides_other_users_job(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="其他人的任务",
            content="不应让无关主管看到。",
        ),
        requested_by=database_user.id,
    )
    database_user.role = "supervisor"
    database_session.commit()

    def use_session() -> Generator[Session, None, None]:
        yield database_session

    def use_other_supervisor() -> UserRecord:
        return UserRecord(
            id="another-supervisor-id",
            email="another-supervisor@example.com",
            display_name="另一个主管",
            password_hash="not-used-in-test",
            role="supervisor",
            is_active=True,
        )

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = use_other_supervisor

    response = client.get(f"/v1/knowledge/jobs/{job.id}")

    assert response.status_code == 404


# 这个测试负责确认普通 agent 不能查询知识库管理任务。
def test_get_knowledge_job_forbids_agent(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    database_user.role = "agent"
    database_session.commit()

    def use_session() -> Generator[Session, None, None]:
        yield database_session

    def use_user() -> UserRecord:
        return database_user

    app.dependency_overrides[get_session] = use_session
    app.dependency_overrides[get_current_user] = use_user

    response = client.get("/v1/knowledge/jobs/missing-job-id")

    assert response.status_code == 403
