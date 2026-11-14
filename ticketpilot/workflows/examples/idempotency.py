"""
文件职责：演示数据库事务与操作编号如何防止重复生效。
使用方式：uv run python -m ticketpilot.workflows.examples.idempotency。
作用与配合：为后续审批执行节点接入幂等机制提供示例。
边界：只写独立的 SQLite 演示表，不修改真实工单，
暂未接入 LangGraph，也不处理外部通知。
"""

import sqlite3
from pathlib import Path


def initialize_database(
    connection: sqlite3.Connection,
) -> None:
    """创建模拟效果表与操作结果表，保留已有演示数据。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS demo_effects (
            ticket_id TEXT PRIMARY KEY,
            execution_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS demo_operations (
            operation_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            result TEXT NOT NULL
        );
        """
    )


def execute_once(
    connection: sqlite3.Connection,
    operation_id: str,
    ticket_id: str,
) -> str:
    """执行一次模拟操作；同编号同参数重试时返回已有结果。"""

    # 正常离开时提交；发生异常时回滚。
    with connection:
        # 在查询操作记录之前，先开始写事务。
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            """
            SELECT ticket_id, result
            FROM demo_operations
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()

        if existing is not None:
            saved_ticket_id, saved_result = existing

            # 同一操作编号不能悄悄改成操作另一个工单。
            if saved_ticket_id != ticket_id:
                raise ValueError(
                    "同一操作编号不能用于不同工单"
                )

            # 已经成功过，直接返回原结果，不增加执行次数。
            return saved_result

        # 用计数器代表一次业务效果。
        # 第一次创建计数；后续新的操作让计数加一。
        connection.execute(
            """
            INSERT INTO demo_effects (
                ticket_id,
                execution_count
            )
            VALUES (?, 1)
            ON CONFLICT(ticket_id)
            DO UPDATE SET
                execution_count = execution_count + 1
            """,
            (ticket_id,),
        )

        result = f"模拟操作完成：{ticket_id}"

        # 在同一事务里保存本次操作的结果。
        connection.execute(
            """
            INSERT INTO demo_operations (
                operation_id,
                ticket_id,
                result
            )
            VALUES (?, ?, ?)
            """,
            (operation_id, ticket_id, result),
        )

        # 离开 with 时先提交事务，再把结果交给调用方。
        return result


def main() -> None:
    """连续提交相同请求，检查业务效果是否只发生一次。"""

    project_root = Path(__file__).resolve().parents[3]
    database_path = project_root / "data" / "idempotency_demo.sqlite"
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        database_path,
        timeout=10,
    )

    try:
        initialize_database(connection)

        first = execute_once(
            connection,
            operation_id="op-001",
            ticket_id="DEMO-001",
        )

        second = execute_once(
            connection,
            operation_id="op-001",
            ticket_id="DEMO-001",
        )

        print("第一次结果：", first)
        print("第二次结果：", second)

        row = connection.execute(
            """
            SELECT execution_count
            FROM demo_effects
            WHERE ticket_id = ?
            """,
            ("DEMO-001",),
        ).fetchone()

        print("实际执行次数：", row[0])

        # 故意复用编号，但改掉目标，观察冲突检查。
        try:
            execute_once(
                connection,
                operation_id="op-001",
                ticket_id="DEMO-999",
            )
        except ValueError as error:
            print("冲突检查：", error)

    finally:
        # sqlite3 的 with connection 管事务，不负责关闭连接。
        connection.close()


if __name__ == "__main__":
    main()