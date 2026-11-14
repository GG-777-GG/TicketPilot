"""文件职责：验证持续 Worker 的启动恢复、领取循环与退出行为。

作用与配合：模拟恢复 Service、Session、模型和等待，检查执行顺序与资源关闭。
使用位置：Worker 命令行相关的单元回归测试。
边界：不连接真实数据库或模型，不执行真实 sleep。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from ticketpilot.knowledge.application.jobs import KnowledgeJobRecoveryError
from ticketpilot.knowledge.workers import worker_cli


@pytest.fixture(autouse=True)
def mock_startup_recovery(
    # pytest 提供的临时替换工具，测试结束后自动恢复原函数。
    monkeypatch: pytest.MonkeyPatch,
) -> MagicMock:
    """循环单元测试默认模拟恢复服务；需要特定结果的测试自行配置。"""
    recover = MagicMock(return_value=())
    monkeypatch.setattr(worker_cli, "recover_expired_ingestion_jobs", recover)
    return recover


def test_watch_continues_after_empty_queue_and_failed_job(monkeypatch, capsys, mock_startup_recovery):
    """空闲时先关闭 Session 再等待；后续任务逐条处理，失败不阻断循环。"""
    contexts = []
    sessions = []
    events = []
    provider = object()
    provider_factory = MagicMock(return_value=provider)

    def create_session_context():
        """每轮提供独立上下文，以检查会话生命周期而非真实数据库连接。"""
        context = MagicMock()
        session = MagicMock(spec=Session)
        context.__enter__.return_value = session
        contexts.append(context)
        sessions.append(session)
        return context

    outcomes = iter([
        None,
        SimpleNamespace(id="failed-a", status="failed"),
        SimpleNamespace(id="succeeded-b", status="succeeded"),
        KeyboardInterrupt(),
    ])

    def process_next(*, session, embedding_provider):
        """按固定顺序模拟任务返回结果，最后中断以结束无限循环。"""
        assert session is sessions[-1]
        assert embedding_provider is provider
        if len(contexts) > 1:
            # 新一轮开始前，上一轮会话必须已关闭。
            contexts[-2].__exit__.assert_called_once()
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        events.append("empty" if outcome is None else outcome.id)
        return outcome

    def idle_wait(seconds):
        """记录等待，不真实 sleep，并检查等待时没有持有上一轮会话。"""
        contexts[-1].__exit__.assert_called_once()
        events.append(("sleep", seconds))

    monkeypatch.setattr(worker_cli, "SessionLocal", create_session_context)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", provider_factory)
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", process_next)
    monkeypatch.setattr(worker_cli, "sleep", idle_wait)

    assert worker_cli.watch_queued_jobs(poll_interval_seconds=5) is None
    assert events == ["empty", ("sleep", 5), "failed-a", "succeeded-b"]
    # 一次启动恢复会话，加上四次任务领取会话，全部独立创建。
    assert len(sessions) == len({id(session) for session in sessions}) == 5
    mock_startup_recovery.assert_called_once()
    assert all(context.__exit__.call_count == 1 for context in contexts)
    provider_factory.assert_called_once()
    output = capsys.readouterr().out
    assert "任务 failed-a: failed" in output
    assert "任务 succeeded-b: succeeded" in output
    assert "Worker 已停止" in output


def test_watch_ctrl_c_during_idle_wait_is_clean(monkeypatch, capsys):
    """等待阶段收到 Ctrl+C 时正常结束，且会话在等待之前已关闭。"""
    context = MagicMock()
    context.__enter__.return_value = MagicMock(spec=Session)
    # 启动恢复和任务领取使用两个独立会话。
    startup_context = MagicMock()
    session_factory = MagicMock(side_effect=[startup_context, context])
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)
    process_next = MagicMock(return_value=None)
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", process_next)

    def interrupt_wait(seconds):
        """模拟在等待中按 Ctrl+C，无实际延时。"""
        assert seconds == 3
        context.__exit__.assert_called_once()
        raise KeyboardInterrupt

    monkeypatch.setattr(worker_cli, "sleep", interrupt_wait)
    worker_cli.watch_queued_jobs()
    startup_context.__exit__.assert_called_once()
    process_next.assert_called_once()
    assert "Worker 已停止" in capsys.readouterr().out


def test_watch_does_not_hide_unexpected_system_error(monkeypatch):
    """意外系统故障向上传递，会话仍正常退出，不假装处理成功。"""
    context = MagicMock()
    # 将恢复会话与发生领取错误的会话分开，分别验证关闭。
    startup_context = MagicMock()
    session_factory = MagicMock(side_effect=[startup_context, context])
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)
    error = RuntimeError("database infrastructure unavailable")
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", MagicMock(side_effect=error))
    wait = MagicMock()
    monkeypatch.setattr(worker_cli, "sleep", wait)
    with pytest.raises(RuntimeError) as caught:
        worker_cli.watch_queued_jobs()
    assert caught.value is error
    startup_context.__exit__.assert_called_once()
    context.__exit__.assert_called_once()
    wait.assert_not_called()


@pytest.mark.parametrize("interval", [0, -1])
def test_watch_rejects_invalid_interval_before_resource_creation(monkeypatch, interval, mock_startup_recovery):
    """无效等待参数应在创建模型、Session 和进入循环之前被拒绝。"""
    provider_factory = MagicMock()
    session_factory = MagicMock()
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", provider_factory)
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)
    with pytest.raises(ValueError, match="至少为 1 秒"):
        worker_cli.watch_queued_jobs(poll_interval_seconds=interval)
    provider_factory.assert_not_called()
    session_factory.assert_not_called()
    mock_startup_recovery.assert_not_called()


@pytest.mark.parametrize(
    ("args", "interval"),
    [(["--watch"], 3), (["--watch", "--poll-interval", "5"], 5)],
)
def test_cli_dispatches_watch_without_starting_other_modes(monkeypatch, args, interval):
    """命令参数传递正确，持续模式正常返回后不进入单条或批量分支。"""
    watch = MagicMock()
    session_factory = MagicMock()
    batch = MagicMock()
    monkeypatch.setattr(worker_cli, "watch_queued_jobs", watch)
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_cli, "process_queued_jobs", batch)
    assert worker_cli.main(args) == 0
    watch.assert_called_once_with(poll_interval_seconds=interval)
    session_factory.assert_not_called()
    batch.assert_not_called()


@pytest.mark.parametrize("args", [
    ["job-id", "--watch"],
    ["--watch", "--max-jobs", "2"],
    ["--poll-interval", "5"],
    ["job-id", "--poll-interval", "5"],
    ["--max-jobs", "2", "--poll-interval", "5"],
    ["--watch", "--poll-interval", "0"],
    ["--watch", "--poll-interval", "-1"],
    ["--watch", "--poll-interval", "abc"],
    ["--watch", "--poll-interval", "1.5"],
])
def test_cli_rejects_invalid_watch_arguments_without_side_effects(monkeypatch, args):
    """参数冲突和非法秒数由解析阶段拒绝，不启动循环或创建数据库资源。"""
    watch = MagicMock()
    session_factory = MagicMock()
    batch = MagicMock()
    monkeypatch.setattr(worker_cli, "watch_queued_jobs", watch)
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_cli, "process_queued_jobs", batch)
    with pytest.raises(SystemExit) as caught:
        worker_cli.main(args)
    assert caught.value.code == 2
    watch.assert_not_called()
    session_factory.assert_not_called()
    batch.assert_not_called()


def test_startup_recovery_finishes_before_first_claim(
    # 临时替换 Session、领取与等待函数。
    monkeypatch: pytest.MonkeyPatch,
    # 读取终端输出，验证恢复数量提示。
    capsys: pytest.CaptureFixture[str],
    # 当前测试使用的启动恢复模拟函数。
    mock_startup_recovery: MagicMock,
) -> None:
    """恢复完成并关闭会话后才领取任务，空闲退出时处理会话也已关闭。"""
    events = []
    recovery_context = MagicMock()
    recovery_session = MagicMock(spec=Session)
    recovery_context.__enter__.return_value = recovery_session
    processing_context = MagicMock()
    processing_session = MagicMock(spec=Session)
    processing_context.__enter__.return_value = processing_session
    session_factory = MagicMock(side_effect=[recovery_context, processing_context])
    monkeypatch.setattr(worker_cli, "SessionLocal", session_factory)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", object)

    def recover(
        # Worker 传给恢复 Service 的 Session、当前时间和数量上限。
        **kwargs: object,
    ) -> tuple[str, ...]:
        """检查恢复调用参数，返回两条已恢复任务的 ID。"""
        assert kwargs["session"] is recovery_session
        assert kwargs["limit"] == 100
        assert kwargs["now"].tzinfo is not None
        assert kwargs["now"].utcoffset().total_seconds() == 0
        events.append("recover")
        return ("expired-running", "expired-cancelling")

    def claim(
        # Worker 本次领取操作使用的参数。
        **kwargs: object,
    ) -> None:
        """首次领取前，启动恢复会话必须已经关闭。"""
        recovery_context.__exit__.assert_called_once()
        assert kwargs["session"] is processing_session
        events.append("claim")
        return None

    def stop(
        # 本次空闲等待的秒数；测试中不真实等待。
        _seconds: int,
    ) -> None:
        """确认关闭处理会话后，用 Ctrl+C 结束持续循环。"""
        processing_context.__exit__.assert_called_once()
        raise KeyboardInterrupt

    mock_startup_recovery.side_effect = recover
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", claim)
    monkeypatch.setattr(worker_cli, "sleep", stop)

    worker_cli.watch_queued_jobs()

    assert events == ["recover", "claim"]
    mock_startup_recovery.assert_called_once()
    recovery_context.__enter__.assert_called_once()
    processing_context.__enter__.assert_called_once()
    assert "恢复了 2 条" in capsys.readouterr().out


def test_startup_recovery_failure_prevents_claim(
    # 替换会话、模型构造和领取函数。
    monkeypatch: pytest.MonkeyPatch,
    # 模拟启动恢复时的数据库故障。
    mock_startup_recovery: MagicMock,
) -> None:
    """恢复错误向上传递，不继续创建模型或领取，恢复会话仍被关闭。"""
    context = MagicMock()
    monkeypatch.setattr(worker_cli, "SessionLocal", lambda: context)
    error = KnowledgeJobRecoveryError("recovery database unavailable")
    mock_startup_recovery.side_effect = error
    claim = MagicMock()
    provider_factory = MagicMock()
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", claim)
    monkeypatch.setattr(worker_cli, "OllamaEmbeddingProvider", provider_factory)

    with pytest.raises(KnowledgeJobRecoveryError) as caught:
        worker_cli.watch_queued_jobs()

    assert caught.value is error
    claim.assert_not_called()
    provider_factory.assert_not_called()
    context.__exit__.assert_called_once()


def test_ctrl_c_during_startup_recovery_is_clean(
    # 替换会话和领取函数。
    monkeypatch: pytest.MonkeyPatch,
    # 检查是否打印了正常停止提示。
    capsys: pytest.CaptureFixture[str],
    # 在恢复调用处模拟用户按 Ctrl+C。
    mock_startup_recovery: MagicMock,
) -> None:
    """恢复阶段中断也通过原来的 Ctrl+C 分支退出。"""
    context = MagicMock()
    monkeypatch.setattr(worker_cli, "SessionLocal", lambda: context)
    mock_startup_recovery.side_effect = KeyboardInterrupt
    claim = MagicMock()
    monkeypatch.setattr(worker_cli, "process_next_ingestion_job", claim)

    worker_cli.watch_queued_jobs()

    claim.assert_not_called()
    context.__exit__.assert_called_once()
    assert "Worker 已停止" in capsys.readouterr().out
