"""验证入库任务租约迁移保留旧任务，且能够安全回滚。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "24f11cf16318"
LEASE_REVISION = "263e791b7093"


# 这个测试负责确认旧任务升级后仍可读取，并且新增字段可用。
def test_lease_migration_preserves_existing_job(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'job_lease.db').as_posix()}"
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.attributes["database_url"] = database_url

    # 先建立没有租约字段的旧版任务表。
    command.upgrade(alembic_config, PREVIOUS_REVISION)
    engine = create_engine(database_url)

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, display_name, password_hash) "
                    "VALUES ('user-001', 'user@example.com', 'Test User', 'hash')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_ingestion_jobs "
                    "(id, title, raw_content, source_type, visibility, requested_by, "
                    "created_at, updated_at) "
                    "VALUES ('job-001', 'Legacy Job', 'content', 'manual', "
                    "'internal', 'user-001', :created_at, :updated_at)"
                ),
                {
                    "created_at": "2026-10-14 12:00:00",
                    "updated_at": "2026-10-14 12:00:00",
                }
            )

        command.upgrade(alembic_config, LEASE_REVISION)
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("knowledge_ingestion_jobs")
        }
        assert columns["lease_token"]["nullable"] is True
        assert columns["lease_expires_at"]["nullable"] is True

        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT lease_token, lease_expires_at "
                    "FROM knowledge_ingestion_jobs WHERE id = 'job-001'"
                )
            ).one() == (None, None)
            connection.execute(
                text(
                    "UPDATE knowledge_ingestion_jobs "
                    "SET lease_token = :token, lease_expires_at = :expires_at "
                    "WHERE id = 'job-001'"
                ),
                {
                    "token": "lease-001",
                    "expires_at": "2026-10-14 12:30:00",
                },
            )
            assert connection.scalar(
                text(
                    "SELECT lease_token FROM knowledge_ingestion_jobs "
                    "WHERE id = 'job-001'"
                )
            ) == "lease-001"

        # 回滚只移除新增字段，不应删除原有任务。
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        remaining_columns = {
            column["name"]
            for column in inspect(engine).get_columns("knowledge_ingestion_jobs")
        }
        assert "lease_token" not in remaining_columns
        assert "lease_expires_at" not in remaining_columns
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT title FROM knowledge_ingestion_jobs "
                    "WHERE id = 'job-001'"
                )
            ) == "Legacy Job"
    finally:
        engine.dispose()
