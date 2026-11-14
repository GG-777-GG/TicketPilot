"""
文件职责：验证执行结果表迁移、约束、默认值及已有数据保留。
作用与配合：在临时SQLite升级/回滚，并离线生成PostgreSQL建表SQL。
边界：不连接或升级业务数据库，回滚只作用于临时文件。
"""

from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "6cfe2a7d9b41"
EXECUTION_REVISION = "91b7e4c2a6d0"


def test_execution_upgrade_and_downgrade_preserve_existing_records(tmp_path: Path) -> None:
    """新增/移除结果表时，原用户、工单及审批申请整行保持不变。"""
    database_url = f"sqlite:///{(tmp_path / 'executions.sqlite').as_posix()}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        """每个测试连接都实际执行外键检查。"""
        connection.execute("PRAGMA foreign_keys=ON")

    def original_rows():
        """只读取旧表，比较升级和回滚是否更改原记录。"""
        with engine.connect() as connection:
            return tuple(
                tuple(tuple(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")))
                for table in ("users", "tickets", "approval_requests")
            )

    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (id,email,display_name,password_hash) VALUES "
                "('owner','owner@example.com','Owner','test-hash'),"
                "('executor','executor@example.com','Executor','test-hash')"
            ))
            connection.execute(text(
                "INSERT INTO tickets (id,customer_name,contact_email,channel,subject,description,priority,status) "
                "VALUES ('ticket','Customer','customer@example.com','web','Keep title','Keep content','medium','open')"
            ))
            connection.execute(text(
                "INSERT INTO approval_requests (id,operation_id,ticket_id,requested_by,request_reason,status) "
                "VALUES ('approval','operation','ticket','owner','Keep reason','approved')"
            ))
        original = original_rows()
        command.upgrade(config, EXECUTION_REVISION)
        inspector = inspect(engine)
        assert "approval_executions" in inspector.get_table_names()
        assert inspector.get_pk_constraint("approval_executions")["constrained_columns"] == ["operation_id"]
        references = {tuple(item["constrained_columns"]): (item["referred_table"], item["referred_columns"], item["options"].get("ondelete")) for item in inspector.get_foreign_keys("approval_executions")}
        assert references[("operation_id",)] == ("approval_requests", ["operation_id"], "RESTRICT")
        assert references[("ticket_id",)] == ("tickets", ["id"], "RESTRICT")
        assert references[("executed_by",)] == ("users", ["id"], "RESTRICT")
        assert original_rows() == original

        # 直接SQL省略默认字段，验证实际迁移而非ORM默认行为。
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO approval_executions (operation_id,ticket_id,executed_by,result) "
                "VALUES ('operation','ticket','executor','Closed result')"
            ))
            row = connection.execute(text("SELECT * FROM approval_executions")).mappings().one()
            assert row["action"] == "close_ticket"
            assert row["created_at"] is not None and row["result"] == "Closed result"

        for statement in (
            "INSERT INTO approval_executions (operation_id,ticket_id,executed_by,result) VALUES ('operation','ticket','executor','Duplicate')",
            "UPDATE approval_executions SET operation_id='approval'",
            "UPDATE approval_executions SET ticket_id='missing'",
            "UPDATE approval_executions SET executed_by='missing'",
            "UPDATE approval_executions SET action='delete_ticket'",
            "UPDATE approval_executions SET result=NULL",
            "DELETE FROM approval_requests WHERE id='approval'",
            "DELETE FROM users WHERE id='executor'",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text(statement))

        command.downgrade(config, PREVIOUS_REVISION)
        assert "approval_executions" not in inspect(engine).get_table_names()
        assert "approval_requests" in inspect(engine).get_table_names()
        assert original_rows() == original
    finally:
        engine.dispose()


def test_execution_migration_generates_postgresql_sql_without_connecting() -> None:
    """检查正式目标方言能生成约束，无需连接实际PostgreSQL。"""
    output = StringIO()
    config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = "postgresql+psycopg://localhost/ddl_check"
    command.upgrade(config, f"{PREVIOUS_REVISION}:{EXECUTION_REVISION}", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE approval_executions" in sql
    assert "PRIMARY KEY (operation_id)" in sql
    assert "REFERENCES approval_requests (operation_id)" in sql
    assert sql.count("ON DELETE RESTRICT") == 3
