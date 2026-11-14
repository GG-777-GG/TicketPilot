"""
文件职责：验证审计表迁移、结果约束、索引和旧数据保留。
作用与配合：临时SQLite升级/回滚及PostgreSQL离线DDL。
边界：不连接或升级真实业务数据库，不在真实库执行downgrade。
"""

from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "91b7e4c2a6d0"
CURRENT = "a31d9b7e2406"


def test_audit_migration_preserves_business_rows(tmp_path):
    """升级/回滚只改变审计表，默认时间、索引和约束实际生效。"""
    config = Config(str(ROOT / "alembic.ini"))
    url = "sqlite:///" + (tmp_path / "audit.sqlite").as_posix()
    config.attributes["database_url"] = url
    command.upgrade(config, PREVIOUS)
    engine = create_engine(url)
    def original_rows():
        """比较原业务表全部列，避免只验证表名仍在。"""
        with engine.connect() as connection:
            return tuple(tuple(tuple(row) for row in connection.execute(text(f"SELECT * FROM {table}")))
                         for table in ("users", "tickets", "approval_requests", "approval_executions"))
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users(id,email,display_name,password_hash) VALUES('owner','owner@example.com','Owner','test-hash')"))
            connection.execute(text("INSERT INTO tickets(id,customer_name,contact_email,channel,subject,description,priority,status) VALUES('ticket','Customer','test@example.com','web','Keep title','Keep body','medium','open')"))
            connection.execute(text("INSERT INTO approval_requests(id,operation_id,ticket_id,requested_by,request_reason) VALUES('approval','operation','ticket','owner','Keep reason')"))
        before = original_rows()
        command.upgrade(config, CURRENT)
        inspector = inspect(engine)
        assert "audit_events" in inspector.get_table_names()
        assert inspector.get_foreign_keys("audit_events") == []
        assert inspector.get_pk_constraint("audit_events")["constrained_columns"] == ["id"]
        assert {item["name"] for item in inspector.get_indexes("audit_events")} == {
            "ix_audit_events_actor_id", "ix_audit_events_operation_id", "ix_audit_events_occurred_at",
        }
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO audit_events(id,event_type,outcome,actor_id,operation_id) VALUES('audit-1','ticket.closed','succeeded','historical-user','operation')"))
            connection.execute(text("INSERT INTO audit_events(id,event_type,outcome,operation_id) VALUES('audit-2','execution.replayed','replayed','operation')"))
            rows = connection.execute(text("SELECT * FROM audit_events ORDER BY id")).mappings().all()
            assert len(rows) == 2 and rows[0]["occurred_at"] is not None
            assert rows[0]["actor_id"] == "historical-user" and rows[1]["actor_id"] is None
        for statement in (
            "UPDATE audit_events SET outcome='unknown' WHERE id='audit-1'",
            "UPDATE audit_events SET outcome=NULL WHERE id='audit-1'",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text(statement))
        assert original_rows() == before
        command.downgrade(config, PREVIOUS)
        assert "audit_events" not in inspect(engine).get_table_names()
        assert original_rows() == before
    finally:
        engine.dispose()


def test_audit_migration_compiles_for_postgresql_without_connection():
    """验证正式方言可生成DDL，不连接PostgreSQL。"""
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = "postgresql+psycopg://localhost/ddl_check"
    command.upgrade(config, f"{PREVIOUS}:{CURRENT}", sql=True)
    ddl = output.getvalue()
    assert "CREATE TABLE audit_events" in ddl
    assert "TIMESTAMP WITH TIME ZONE" in ddl and "DEFAULT now()" in ddl
    assert "ck_audit_events_outcome" in ddl and "PRIMARY KEY (id)" in ddl
    assert "CREATE INDEX ix_audit_events_operation_id" in ddl
    assert "REFERENCES" not in ddl
