"""验证提交入库任务只保存待处理输入，不立即生成知识文档。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ticketpilot.identity.models import UserRecord
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.application.jobs import submit_ingestion_job
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
    KnowledgeIngestionJobRecord,
)


# 这个测试负责确认提交任务后，原始输入已持久化且任务等待处理。
def test_submit_ingestion_job_persists_queued_input(
    database_session: Session,
    database_user: UserRecord,
) -> None:
    raw_document = RawDocument(
        title="密码重置流程",
        content="员工忘记密码后，应通过公司邮箱重置。",
    )

    job = submit_ingestion_job(
        session=database_session,
        raw_document=raw_document,
        requested_by=database_user.id,
        visibility="restricted",
    )

    # Service 必须提交事务，后续 Worker 才能读到这条任务。
    assert database_session.in_transaction() is False
    database_session.expire_all()
    saved = database_session.get(KnowledgeIngestionJobRecord, job.id)

    assert saved is not None
    assert saved.title == raw_document.title
    assert saved.raw_content == raw_document.content
    assert saved.source_type == "manual"
    assert saved.source_uri is None
    assert saved.visibility == "restricted"
    assert saved.requested_by == database_user.id
    assert saved.status == "queued"
    assert saved.attempt_count == 0
    assert saved.document_id is None
    assert saved.last_error is None

    # 提交任务不等于执行任务，因此此时还没有知识文档。
    document_count = database_session.scalar(
        select(func.count(KnowledgeDocumentRecord.id))
    )
    assert document_count == 0
