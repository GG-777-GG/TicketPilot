"""测试过期任务恢复 Service 在数据库失败时负责回滚。"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from ticketpilot.knowledge.application import jobs
from ticketpilot.knowledge.persistence.job_repository import KnowledgeJobRepositoryError


# 这个测试负责确认 Repository 报错时 Service 会回滚并统一转换异常。
def test_recover_expired_job_rolls_back_repository_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock(spec=Session)

    def raise_database_error(*_args: object, **_kwargs: object) -> None:
        raise KnowledgeJobRepositoryError("database unavailable")

    monkeypatch.setattr(jobs, "get_ingestion_job", raise_database_error)

    with pytest.raises(jobs.KnowledgeJobRecoveryError, match="恢复租约过期"):
        jobs.recover_expired_ingestion_job(
            session=session,
            job_id="test-job-id",
            now=datetime(2026, 10, 15, tzinfo=timezone.utc),
        )

    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
