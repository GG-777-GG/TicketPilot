"""测试过期知识入库任务的独立命令行入口。"""

from importlib import import_module
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session


# 这个测试负责确认命令行调用恢复 Service 并报告恢复数量。
def test_recovery_cli_runs_bounded_recovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recovery_cli = import_module("ticketpilot.knowledge.workers.recovery_cli")
    session = MagicMock(spec=Session)
    session_context = MagicMock()
    session_context.__enter__.return_value = session
    recover = MagicMock(return_value=("job-one", "job-two"))
    monkeypatch.setattr(recovery_cli, "SessionLocal", lambda: session_context)
    monkeypatch.setattr(recovery_cli, "recover_expired_ingestion_jobs", recover)

    assert recovery_cli.main(["--limit", "3"]) == 0

    session_context.__enter__.assert_called_once_with()
    session_context.__exit__.assert_called_once()
    recover.assert_called_once()
    kwargs = recover.call_args.kwargs
    assert kwargs["session"] is session
    assert kwargs["limit"] == 3
    assert kwargs["now"].tzinfo is not None
    assert kwargs["now"].utcoffset().total_seconds() == 0
    assert "2" in capsys.readouterr().out


# 这个测试负责确认无效 limit 会在创建数据库 Session 前被拒绝。
@pytest.mark.parametrize("limit", ["0", "-1"])
def test_recovery_cli_rejects_nonpositive_limit_before_opening_session(
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    recovery_cli = import_module("ticketpilot.knowledge.workers.recovery_cli")
    session_factory = MagicMock()
    monkeypatch.setattr(recovery_cli, "SessionLocal", session_factory)

    with pytest.raises(SystemExit) as error:
        recovery_cli.main(["--limit", limit])

    assert error.value.code == 2
    session_factory.assert_not_called()
