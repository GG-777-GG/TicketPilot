"""测试单条和批量入库任务的命令行入口，不连接真实数据库或 Ollama。"""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from ticketpilot.knowledge.workers import worker_cli


# 这个测试负责确认独立启动 Worker 时会登记文档外键引用的 users 表。
def test_worker_cli_registers_user_model_in_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import ticketpilot.knowledge.workers.worker_cli; "
                "from ticketpilot.core.database import Base; "
                "assert 'users' in Base.metadata.tables"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


# 这个测试负责确认命令行会交给 Worker 处理指定任务。
def test_worker_cli_processes_one_job(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = MagicMock(spec=Session)
    session_context = MagicMock()
    session_context.__enter__.return_value = session
    provider = object()
    received: dict[str, object] = {}

    monkeypatch.setattr(worker_cli, "SessionLocal", lambda: session_context)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", lambda: provider)

    def fake_process_ingestion_job(**kwargs: object) -> SimpleNamespace:
        received.update(kwargs)
        return SimpleNamespace(status="succeeded", id="test-job-id")

    monkeypatch.setattr(
        worker_cli,
        "process_ingestion_job",
        fake_process_ingestion_job,
    )

    exit_code = worker_cli.main(["test-job-id"])

    assert exit_code == 0
    assert received == {
        "session": session,
        "job_id": "test-job-id",
        "embedding_provider": provider,
    }
    session_context.__exit__.assert_called_once()
    assert "succeeded" in capsys.readouterr().out


# 这个测试负责确认不存在或不能领取的任务不会假装成功。
def test_worker_cli_reports_job_not_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_context = MagicMock()
    session_context.__enter__.return_value = MagicMock(spec=Session)
    monkeypatch.setattr(worker_cli, "SessionLocal", lambda: session_context)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)
    monkeypatch.setattr(
        worker_cli,
        "process_ingestion_job",
        lambda **_kwargs: None,
    )

    assert worker_cli.main(["missing-job-id"]) == 1
    session_context.__exit__.assert_called_once()


# 这个测试负责确认任务失败时命令行返回非零退出码。
def test_worker_cli_reports_failed_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_context = MagicMock()
    session_context.__enter__.return_value = MagicMock(spec=Session)
    monkeypatch.setattr(worker_cli, "SessionLocal", lambda: session_context)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)
    monkeypatch.setattr(
        worker_cli,
        "process_ingestion_job",
        lambda **_kwargs: SimpleNamespace(status="failed"),
    )

    assert worker_cli.main(["test-job-id"]) == 2
    session_context.__exit__.assert_called_once()


# 这个测试负责确认省略 ID 时会自动处理下一条任务。
def test_worker_cli_processes_next_job_without_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = MagicMock(spec=Session)
    session_context = MagicMock()
    session_context.__enter__.return_value = session
    provider = object()
    process_next = MagicMock(
        return_value=SimpleNamespace(
            id="auto-job-id",
            status="succeeded",
        )
    )
    process_by_id = MagicMock()
    monkeypatch.setattr(worker_cli, "SessionLocal", lambda: session_context)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", lambda: provider)
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", process_next)
    monkeypatch.setattr(worker_cli, "process_ingestion_job", process_by_id)

    assert worker_cli.main([]) == 0
    process_next.assert_called_once_with(
        session=session,
        embedding_provider=provider,
    )
    process_by_id.assert_not_called()
    session_context.__exit__.assert_called_once()
    assert "任务 auto-job-id: succeeded" in capsys.readouterr().out


# 这个测试负责确认空队列是正常情况，不会误报失败。
def test_worker_cli_exits_successfully_for_empty_queue(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session_context = MagicMock()
    session_context.__enter__.return_value = MagicMock(spec=Session)
    process_next = MagicMock(return_value=None)
    monkeypatch.setattr(worker_cli, "SessionLocal", lambda: session_context)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", process_next)

    assert worker_cli.main([]) == 0
    process_next.assert_called_once()
    session_context.__exit__.assert_called_once()
    assert "没有领取到待处理任务" in capsys.readouterr().out


# 这个测试负责确认自动处理的任务失败时退出码仍为 2。
def test_worker_cli_reports_failed_auto_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_context = MagicMock()
    session_context.__enter__.return_value = MagicMock(spec=Session)
    monkeypatch.setattr(worker_cli, "SessionLocal", lambda: session_context)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)
    monkeypatch.setattr(
        worker_cli,
        "process_next_ingestion_job",
        lambda **_kwargs: SimpleNamespace(id="failed-job-id", status="failed"),
    )

    assert worker_cli.main([]) == 2
    session_context.__exit__.assert_called_once()


# 这个测试负责确认批量处理遵守数量上限，且每条任务使用独立 Session。
def test_process_queued_jobs_stops_at_limit_with_fresh_sessions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    contexts: list[MagicMock] = []
    sessions: list[MagicMock] = []
    provider = object()

    def fake_session_local() -> MagicMock:
        context = MagicMock()
        session = MagicMock(spec=Session)
        context.__enter__.return_value = session
        contexts.append(context)
        sessions.append(session)
        return context

    process_next = MagicMock(
        side_effect=[
            SimpleNamespace(id="job-1", status="failed"),
            SimpleNamespace(id="job-2", status="succeeded"),
            SimpleNamespace(id="job-3", status="succeeded"),
        ]
    )
    monkeypatch.setattr(worker_cli, "SessionLocal", fake_session_local)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", lambda: provider)
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", process_next)

    assert worker_cli.process_queued_jobs(max_jobs=2) == 2

    assert process_next.call_count == 2
    assert process_next.call_args_list[0].kwargs == {
        "session": sessions[0],
        "embedding_provider": provider,
    }
    assert process_next.call_args_list[1].kwargs == {
        "session": sessions[1],
        "embedding_provider": provider,
    }
    assert sessions[0] is not sessions[1]
    assert all(context.__exit__.call_count == 1 for context in contexts)
    output = capsys.readouterr().out
    assert "任务 job-1: failed" in output
    assert "任务 job-2: succeeded" in output
    assert "job-3" not in output


# 这个测试负责确认队列提前变空时，不会继续创建多余 Session。
def test_process_queued_jobs_stops_when_queue_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts: list[MagicMock] = []

    def fake_session_local() -> MagicMock:
        context = MagicMock()
        context.__enter__.return_value = MagicMock(spec=Session)
        contexts.append(context)
        return context

    process_next = MagicMock(
        side_effect=[
            SimpleNamespace(id="job-1", status="succeeded"),
            None,
        ]
    )
    monkeypatch.setattr(worker_cli, "SessionLocal", fake_session_local)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", process_next)

    assert worker_cli.process_queued_jobs(max_jobs=5) == 1
    assert process_next.call_count == 2
    assert len(contexts) == 2
    assert all(context.__exit__.call_count == 1 for context in contexts)


# 这个测试负责确认非法任务上限会在创建资源前被拒绝。
@pytest.mark.parametrize("max_jobs", [0, -1])
def test_process_queued_jobs_rejects_nonpositive_limit(
    monkeypatch: pytest.MonkeyPatch,
    max_jobs: int,
) -> None:
    session_factory = MagicMock()
    provider_factory = MagicMock()
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", provider_factory)

    with pytest.raises(ValueError, match="max_jobs 必须大于 0"):
        worker_cli.process_queued_jobs(max_jobs=max_jobs)

    session_factory.assert_not_called()
    provider_factory.assert_not_called()


# 这个测试负责确认 --max-jobs 会交给批量函数，不走原来的单条分支。
def test_worker_cli_runs_bounded_batch_with_max_jobs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    process_batch = MagicMock(return_value=2)
    process_next = MagicMock()
    session_factory = MagicMock()
    monkeypatch.setattr(worker_cli, "process_queued_jobs", process_batch)
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", process_next)
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)

    assert worker_cli.main(["--max-jobs", "3"]) == 0

    process_batch.assert_called_once_with(max_jobs=3)
    process_next.assert_not_called()
    session_factory.assert_not_called()
    assert "本次尝试处理 2 条任务" in capsys.readouterr().out


# 这个测试负责确认批量数量必须是正整数，不能等到创建资源后才报错。
@pytest.mark.parametrize(
    ("max_jobs", "expected_error"),
    [
        ("0", "--max-jobs 必须大于 0"),
        ("-1", "--max-jobs 必须大于 0"),
        ("abc", "invalid int value"),
    ],
)
def test_worker_cli_rejects_invalid_batch_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    max_jobs: str,
    expected_error: str,
) -> None:
    process_batch = MagicMock()
    session_factory = MagicMock()
    monkeypatch.setattr(worker_cli, "process_queued_jobs", process_batch)
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)

    with pytest.raises(SystemExit) as error:
        worker_cli.main(["--max-jobs", max_jobs])

    assert error.value.code == 2
    assert expected_error in capsys.readouterr().err
    process_batch.assert_not_called()
    session_factory.assert_not_called()


# 这个测试负责确认指定 ID 和批量模式不能同时使用。
def test_worker_cli_rejects_job_id_with_batch_mode(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    process_batch = MagicMock()
    process_by_id = MagicMock()
    monkeypatch.setattr(worker_cli, "process_queued_jobs", process_batch)
    monkeypatch.setattr(worker_cli, "process_ingestion_job", process_by_id)

    with pytest.raises(SystemExit) as error:
        worker_cli.main(["job-1", "--max-jobs", "3"])

    assert error.value.code == 2
    assert "任务 ID 不能与 --max-jobs 同时使用" in capsys.readouterr().err
    process_batch.assert_not_called()
    process_by_id.assert_not_called()
