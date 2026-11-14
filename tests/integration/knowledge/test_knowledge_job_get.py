"""验证客户端以后可以按 ID 查询持久化的入库任务。"""

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import get_ingestion_job
from ticketpilot.knowledge.application.jobs import submit_ingestion_job


# 这个测试负责确认可从数据库重新查询任务状态与提交人。
def test_get_ingestion_job_returns_saved_job(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="状态查询说明",
            content="客户端稍后用任务 ID 查询进度。",
        ),
        requested_by=database_user.id,
        visibility="restricted",
    )
    database_session.expire_all()

    found = get_ingestion_job(database_session, job.id)

    assert found is not None
    assert found.id == job.id
    assert found.title == "状态查询说明"
    assert found.status == "queued"
    assert found.visibility == "restricted"
    assert found.requested_by == database_user.id
    assert found.document_id is None
    assert database_session.in_transaction() is True


# 这个测试负责确认不存在的任务返回 None，而不是凭空创建。
def test_get_ingestion_job_returns_none_for_missing_id(
    database_session: Session,
) -> None:
    assert get_ingestion_job(
        database_session, "missing-job-id"
    ) is None
