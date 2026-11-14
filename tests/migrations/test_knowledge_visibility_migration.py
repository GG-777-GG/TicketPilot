"""验证权限字段迁移不会丢失旧知识文档，并能拒绝未知可见性。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "baf42e466d80"


# 这个测试负责验证旧文档升级后的默认权限、约束和回滚。
def test_visibility_migration_preserves_existing_document(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'visibility.db').as_posix()}"
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.attributes["database_url"] = database_url

    # 先建立新权限字段出现以前的数据库结构。
    command.upgrade(alembic_config, PREVIOUS_REVISION)
    engine = create_engine(database_url)

    try:
        # 旧文档原本没有 visibility；升级不能使它消失或变成不可读。
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, display_name, password_hash) "
                    "VALUES ('user-001', 'user@example.com', 'Test User', 'hash')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "(id, title, raw_content, cleaned_content, content_hash, "
                    "embedding_model, cleaner_version, chunker_version, "
                    "created_by, created_at, updated_at) "
                    "VALUES ('document-001', 'Legacy Knowledge', 'content', "
                    "'content', :content_hash, 'test-embedding', 'basic-v1', "
                    "'character-v1', 'user-001', :created_at, :updated_at)"
                ),
                {
                    "content_hash": "a" * 64,
                    "created_at": "2026-01-01 00:00:00",
                    "updated_at": "2026-01-01 00:00:00",
                },
            )

        # 升到新版本，旧文档必须自动获得 internal 可见性。
        command.upgrade(alembic_config, "head")
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("knowledge_documents")
        }
        assert columns["visibility"]["nullable"] is False

        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT visibility FROM knowledge_documents "
                    "WHERE id = 'document-001'"
                )
            ) == "internal"

        # 数据库层必须拒绝不在权限规则中的值。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE knowledge_documents SET visibility = 'external' "
                        "WHERE id = 'document-001'"
                    )
                )

        # 回滚这一版时，旧文档仍在，新增字段被移除。
        command.downgrade(alembic_config, PREVIOUS_REVISION)
        remaining_columns = {
            column["name"]
            for column in inspect(engine).get_columns("knowledge_documents")
        }
        assert "visibility" not in remaining_columns

        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT title FROM knowledge_documents "
                    "WHERE id = 'document-001'"
                )
            ) == "Legacy Knowledge"
    finally:
        engine.dispose()
