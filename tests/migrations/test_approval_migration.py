"""
文件职责：验证审批表迁移的升级、默认值、约束及旧数据保留。
作用与配合：通过正式 Alembic 迁移，在临时 SQLite 文件执行。
边界：不升级本机业务数据库；测试回滚仅删除临时审批表。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "0ea19e381c10"
APPROVAL_REVISION = "6cfe2a7d9b41"


def test_approval_upgrade_and_downgrade_preserve_existing_data(tmp_path: Path) -> None:
    """升级只新增审批表；回滚审批表后原用户与工单不变。"""

    database_url = f"sqlite:///{(tmp_path / 'approvals.sqlite').as_posix()}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, PREVIOUS_REVISION)
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        """确保迁移后写入也验证真实外键约束。"""
        connection.execute("PRAGMA foreign_keys=ON")

    def original_records() -> tuple[tuple, tuple]:
        """读取旧表整行，比较升级与回滚是否改动原数据。"""
        with engine.connect() as connection:
            user = tuple(connection.execute(text("SELECT * FROM users WHERE id='legacy-user'")).one())
            ticket = tuple(connection.execute(text("SELECT * FROM tickets WHERE id='legacy-ticket'")).one())
            return user, ticket

    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (id,email,display_name,password_hash) "
                "VALUES ('legacy-user','legacy@example.com','Legacy User','test-hash')"
            ))
            connection.execute(text(
                "INSERT INTO tickets (id,customer_name,contact_email,channel,subject,description,priority,status) "
                "VALUES ('legacy-ticket','Legacy Customer','customer@example.com','web','Legacy Ticket','Keep this content','medium','open')"
            ))
        original = original_records()

        command.upgrade(config, APPROVAL_REVISION)
        inspector = inspect(engine)
        assert "approval_requests" in inspector.get_table_names()
        assert original_records() == original
        assert len(inspector.get_foreign_keys("approval_requests")) == 3
        assert {index["name"] for index in inspector.get_indexes("approval_requests")} == {
            "ix_approval_requests_ticket_id", "ix_approval_requests_status", "ix_approval_requests_requested_by",
        }
        assert any(item["column_names"] == ["operation_id"] for item in inspector.get_unique_constraints("approval_requests"))

        # 直接执行 SQL 并省略状态/操作/时间字段，验证数据库默认值。
        insert_sql = text(
            "INSERT INTO approval_requests (id,operation_id,ticket_id,requested_by,request_reason) "
            "VALUES (:id,:operation_id,:ticket_id,'legacy-user','问题已解决')"
        )
        with engine.begin() as connection:
            connection.execute(insert_sql, {"id": "approval-1", "operation_id": "operation-1", "ticket_id": "legacy-ticket"})
            row = connection.execute(text("SELECT * FROM approval_requests WHERE id='approval-1'")).mappings().one()
            assert row["action"] == "close_ticket"
            assert row["status"] == "pending"
            assert row["created_at"] is not None and row["updated_at"] is not None
            assert row["reviewed_by"] is None and row["reviewed_at"] is None

        # 在迁移创建的表上检查约束，防止模型正确、迁移遗漏规则。
        for sql in [
            "UPDATE approval_requests SET status='unknown'",
            "UPDATE approval_requests SET action='delete_ticket'",
            "UPDATE approval_requests SET requested_by='missing-user'",
            "UPDATE approval_requests SET reviewed_by='missing-user'",
            "UPDATE approval_requests SET ticket_id='missing-ticket'",
            "DELETE FROM tickets WHERE id='legacy-ticket'",
            "DELETE FROM users WHERE id='legacy-user'",
        ]:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text(sql))
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(insert_sql, {"id": "approval-2", "operation_id": "operation-1", "ticket_id": "legacy-ticket"})

        command.downgrade(config, PREVIOUS_REVISION)
        assert "approval_requests" not in inspect(engine).get_table_names()
        assert original_records() == original
    finally:
        engine.dispose()
