"""验证入库任务的 API 响应只暴露客户端需要的状态字段。"""

from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.api.schemas import KnowledgeIngestionJobResponse


# 这个测试负责确认 ORM 任务能转成公开响应且不泄露原文。
def test_ingestion_job_response_serializes_saved_job(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    job = submit_ingestion_job(
        session=database_session,
        raw_document=RawDocument(
            title="状态响应测试",
            content="这段原文应保存在数据库，但不出现在状态响应里。",
        ),
        requested_by=database_user.id,
        visibility="restricted",
    )

    response = KnowledgeIngestionJobResponse.model_validate(job)
    payload = response.model_dump(mode="json")

    assert payload["id"] == job.id
    assert payload["title"] == "状态响应测试"
    assert payload["source_type"] == "manual"
    assert payload["visibility"] == "restricted"
    assert payload["requested_by"] == database_user.id
    assert payload["status"] == "queued"
    assert payload["attempt_count"] == 0
    assert payload["document_id"] is None
    assert payload["last_error"] is None
    assert payload["created_at"]
    assert payload["updated_at"]
    assert "raw_content" not in payload
