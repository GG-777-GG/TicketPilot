"""临时数据库与临时文件协作验证；不访问真实 Ollama 或开发数据库。"""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ticketpilot.knowledge.application.jobs import (
    KnowledgeJobSubmissionError,
    submit_file_ingestion_job,
)
from ticketpilot.knowledge.application.uploads import KnowledgeFileUploadService
from ticketpilot.knowledge.persistence.file_storage import LocalKnowledgeFileStorage
from ticketpilot.knowledge.persistence.models import (
    KnowledgeDocumentRecord,
    KnowledgeIngestionJobRecord,
)


def test_file_submission_is_persisted_without_parsing(
    database_session: Session,  # 独立的内存 SQLite 会话。
    database_user,  # 已创建的测试用户。
    tmp_path: Path,  # 原始文件保存到本题的临时目录。
) -> None:
    """提交后任务可从另一会话读取，文件保持原样，尚未生成知识文档。"""
    content = b"invalid pdf: parsing belongs to worker"
    storage = LocalKnowledgeFileStorage(directory=tmp_path)
    job = submit_file_ingestion_job(
        session=database_session,
        storage=storage,
        upload_service=KnowledgeFileUploadService(),
        title="  维修合同  ",
        filename="contract.pdf",
        content_type="application/pdf",
        content=content,
        requested_by=database_user.id,
        visibility="restricted",
    )
    assert not database_session.in_transaction()
    # 新会话读取，验证任务确实已提交，而非只存在于原 Session 的内存中。
    with Session(database_session.get_bind()) as reader:
        saved = reader.get(KnowledgeIngestionJobRecord, job.id)
        assert saved is not None
        assert saved.title == "维修合同"
        assert saved.status == "queued"
        assert saved.attempt_count == 0
        assert saved.raw_content is None
        assert saved.source_type == "file"
        assert saved.original_filename == saved.source_uri == "contract.pdf"
        assert saved.visibility == "restricted"
        assert saved.requested_by == database_user.id
        assert saved.document_id is None
        assert saved.last_error is None
        assert storage.read(storage_key=saved.storage_key) == content
        assert reader.scalar(select(func.count(KnowledgeDocumentRecord.id))) == 0


def test_actual_foreign_key_failure_cleans_file(
    database_session: Session,
    tmp_path: Path,
) -> None:
    """使用真实外键约束制造 flush 失败，确认事务回滚和文件回收。"""
    with pytest.raises(KnowledgeJobSubmissionError):
        submit_file_ingestion_job(
            session=database_session,
            storage=LocalKnowledgeFileStorage(directory=tmp_path),
            upload_service=KnowledgeFileUploadService(),
            title="合同",
            filename="contract.pdf",
            content_type=None,
            content=b"pending",
            requested_by="missing-user",
            visibility="internal",
        )
    assert list(tmp_path.iterdir()) == []
    assert database_session.scalar(select(func.count(KnowledgeIngestionJobRecord.id))) == 0
