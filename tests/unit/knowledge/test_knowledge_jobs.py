"""测试提交入库任务的事务边界和失败回滚。"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ticketpilot.knowledge.application import jobs
from ticketpilot.knowledge.domain.documents import RawDocument
from ticketpilot.knowledge.persistence.job_repository import KnowledgeJobRepositoryError


# 这个函数负责构造提交任务时使用的原始文档。
def build_raw_document() -> RawDocument:
    return RawDocument(
        title="测试文档",
        content="需要保存的原始正文。",
    )


# 这个测试负责验证 Repository 写入失败时由 Service 回滚。
def test_submit_job_rolls_back_repository_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)

    def fail_to_add_job(_session: Session, _job: object) -> None:
        raise KnowledgeJobRepositoryError("database unavailable")

    monkeypatch.setattr(jobs, "add_ingestion_job", fail_to_add_job)

    with pytest.raises(jobs.KnowledgeJobSubmissionError):
        jobs.submit_ingestion_job(
            session=session,
            raw_document=build_raw_document(),
            requested_by="test-user-id",
        )

    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


# 这个测试负责验证最终提交失败时也必须撤销事务。
def test_submit_job_rolls_back_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)
    session.commit.side_effect = SQLAlchemyError("commit failed")
    monkeypatch.setattr(
        jobs,
        "add_ingestion_job",
        lambda _session, job: job,
    )

    with pytest.raises(jobs.KnowledgeJobSubmissionError):
        jobs.submit_ingestion_job(
            session=session,
            raw_document=build_raw_document(),
            requested_by="test-user-id",
        )

    session.commit.assert_called_once_with()
    session.rollback.assert_called_once_with()
