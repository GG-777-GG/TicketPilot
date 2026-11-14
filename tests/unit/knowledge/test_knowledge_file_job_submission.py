"""验证文件任务提交的失败处理，仅使用临时文件和模拟数据库会话。"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ticketpilot.knowledge.application import jobs
from ticketpilot.knowledge.application.uploads import (
    KnowledgeFileUploadService,
    KnowledgeFileValidationError,
)
from ticketpilot.knowledge.persistence.file_storage import (
    KnowledgeFileStorageError,
    LocalKnowledgeFileStorage,
)
from ticketpilot.knowledge.persistence.job_repository import KnowledgeJobRepositoryError


@pytest.fixture
def submission_args(tmp_path: Path):
    """提供合法提交参数；磁盘文件仅保存在本题的临时目录中。"""
    return dict(
        session=MagicMock(spec=Session),
        storage=LocalKnowledgeFileStorage(directory=tmp_path / "uploads"),
        upload_service=KnowledgeFileUploadService(max_file_size_bytes=100),
        title="  维修合同  ",
        filename="contract.pdf",
        content_type="application/pdf",
        content=b"not parsed during submission",
        requested_by="test-owner",
        visibility="internal",
    )


@pytest.mark.parametrize("overrides", [
    {"title": " "}, {"title": "长" * 201}, {"visibility": "public"},
    {"filename": "../contract.pdf"}, {"content": b""}, {"content": b"x" * 101},
])
def test_invalid_input_creates_neither_file_nor_job(
    submission_args,  # 含临时目录和模拟数据库会话的合法参数。
    overrides,  # 当前题目替换成的非法输入。
    tmp_path: Path,  # pytest 隔离目录。
    monkeypatch: pytest.MonkeyPatch,  # 测试结束自动恢复替换的方法。
) -> None:
    """输入校验失败应在写入文件和数据库之前停止。"""
    add = MagicMock()
    monkeypatch.setattr(jobs, "add_ingestion_job", add)
    with pytest.raises(KnowledgeFileValidationError):
        jobs.submit_file_ingestion_job(**(submission_args | overrides))
    assert not (tmp_path / "uploads").exists()
    add.assert_not_called()
    submission_args["session"].commit.assert_not_called()


def test_save_failure_does_not_start_database_write(submission_args, monkeypatch) -> None:
    """磁盘保存失败时向上报告存储异常，不创建任务。"""
    storage = MagicMock(spec=LocalKnowledgeFileStorage)
    storage.save.side_effect = KnowledgeFileStorageError("disk unavailable")
    add = MagicMock()
    monkeypatch.setattr(jobs, "add_ingestion_job", add)
    with pytest.raises(KnowledgeFileStorageError):
        jobs.submit_file_ingestion_job(**(submission_args | {"storage": storage}))
    add.assert_not_called()
    storage.delete.assert_not_called()


def test_precommit_database_failure_cleans_saved_file(submission_args, tmp_path, monkeypatch) -> None:
    """Repository 失败时回滚并清理本次文件，保留目录中的旧文件。"""
    directory = tmp_path / "uploads"
    directory.mkdir()
    old_file = directory / "old.txt"
    old_file.write_bytes(b"keep")
    error = KnowledgeJobRepositoryError("flush failed")
    monkeypatch.setattr(jobs, "add_ingestion_job", MagicMock(side_effect=error))
    with pytest.raises(jobs.KnowledgeJobSubmissionError) as caught:
        jobs.submit_file_ingestion_job(**submission_args)
    assert caught.value.__cause__ is error
    assert list(directory.iterdir()) == [old_file]
    assert old_file.read_bytes() == b"keep"
    submission_args["session"].rollback.assert_called_once()
    submission_args["session"].commit.assert_not_called()


def test_cleanup_failure_is_reported(submission_args, monkeypatch) -> None:
    """任务写入和文件回收同时失败时，明确暴露清理失败。"""
    storage = MagicMock(spec=LocalKnowledgeFileStorage)
    storage.save.return_value = "a" * 32 + ".bin"
    cleanup_error = KnowledgeFileStorageError("cleanup failed")
    storage.delete.side_effect = cleanup_error
    monkeypatch.setattr(jobs, "add_ingestion_job", MagicMock(side_effect=SQLAlchemyError()))
    with pytest.raises(jobs.KnowledgeJobSubmissionError, match="清理失败") as caught:
        jobs.submit_file_ingestion_job(**(submission_args | {"storage": storage}))
    assert caught.value.__cause__ is cleanup_error
    storage.delete.assert_called_once_with(storage_key=storage.save.return_value)


def test_uncertain_commit_keeps_file_and_identifies_job(submission_args, tmp_path, monkeypatch) -> None:
    """commit 异常时不冒险删除可能已被数据库任务引用的文件。"""
    added = []

    def record_job(session, job):
        """模拟 flush 成功，记录待提交对象，供断言异常中的任务 ID。"""
        added.append(job)
        return job

    monkeypatch.setattr(jobs, "add_ingestion_job", record_job)
    error = SQLAlchemyError("commit acknowledgement lost")
    submission_args["session"].commit.side_effect = error
    with pytest.raises(jobs.KnowledgeJobSubmissionError, match="状态待核实") as caught:
        jobs.submit_file_ingestion_job(**submission_args)
    assert str(added[0].id) in str(caught.value)
    assert caught.value.__cause__ is error
    assert (tmp_path / "uploads" / added[0].storage_key).read_bytes() == submission_args["content"]
    submission_args["session"].rollback.assert_called_once()
