"""
文件职责：验证模拟审批执行在业务提交后失败，恢复时不会重复生效。
使用方式：通过 pytest 单独运行本文件或随集成测试运行。
作用与配合：组合 workflow_approval、幂等操作和 SQLite 文件检查点。
边界：仅使用 tmp_path 下的数据库，模拟节点异常和重新打开文件，
不模拟操作系统强制终止，也不修改真实工单或已有演示数据。
"""

import sqlite3
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.runtime import Runtime
from langgraph.types import Command

from ticketpilot.workflows.examples import approval


pytestmark = pytest.mark.integration


def read_effect_counts(database_path: Path) -> tuple[int, int]:
    """从独立连接读取模拟执行次数与已提交操作记录数。"""

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            """
            SELECT execution_count
            FROM demo_effects
            WHERE ticket_id = ?
            """,
            ("DEMO-001",),
        ).fetchone()
        assert row is not None, "故障应发生在模拟业务已提交之后"

        operation_count = connection.execute(
            "SELECT COUNT(*) FROM demo_operations"
        ).fetchone()[0]

        return row[0], operation_count
    finally:
        connection.close()


def test_committed_operation_is_not_repeated_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模拟业务已提交但节点报错，重开检查点恢复后效果仍只有一次。"""

    checkpoint_path = tmp_path / "checkpoints.sqlite"
    effects_path = tmp_path / "effects.sqlite"
    context = approval.ApprovalContext(
        effects_database_path=effects_path,
    )
    config = {"configurable": {"thread_id": "retry-test"}}
    initial_state: approval.ApprovalState = {
        "ticket_id": "DEMO-001",
        "operation_id": "fixed-operation-001",
        "approved": None,
        "approval_reason": "",
        "result": "",
    }

    # 保存原节点：故障替身仍执行真实的模拟业务事务。
    real_execute = approval.execute_demo

    def fail_after_commit(
        state: approval.ApprovalState,
        runtime: Runtime[approval.ApprovalContext],
    ) -> dict[str, str]:
        """业务事务成功后再抛异常，制造检查点尚未完成的窗口。"""

        real_execute(state, runtime)
        raise RuntimeError("模拟提交后失败")

    with monkeypatch.context() as patch:
        patch.setattr(approval, "execute_demo", fail_after_commit)

        # 替换后再建图，让图注册故障节点。
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            workflow = approval.build_workflow(saver)
            paused = workflow.invoke(
                initial_state,
                config=config,
                context=context,
            )
            assert paused["__interrupt__"]
            assert not effects_path.exists(), "审批前不应执行模拟业务"

            with pytest.raises(RuntimeError, match="模拟提交后失败"):
                workflow.invoke(
                    Command(resume={"approved": True, "reason": "已核验"}),
                    config=config,
                    context=context,
                )

            failed = workflow.get_state(config)
            assert failed.values["approved"] is True
            assert failed.values["operation_id"] == "fixed-operation-001"
            assert failed.next == ("execute_demo",)
            assert any(task.error is not None for task in failed.tasks)

    # 在恢复之前就验证事务确已提交，避免只测试到“写入前失败”。
    assert read_effect_counts(effects_path) == (1, 1)

    # 退出替换块后原函数已恢复；重新打开检查点并建图。
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        workflow = approval.build_workflow(saver)
        result = workflow.invoke(None, config=config, context=context)

        assert result["operation_id"] == "fixed-operation-001"
        assert result["approved"] is True
        assert result["approval_reason"] == "已核验"
        assert result["result"] == "模拟操作完成：DEMO-001"
        assert workflow.get_state(config).next == ()

    # 直接检查业务表，而不只是检查返回文字。
    assert read_effect_counts(effects_path) == (1, 1)
