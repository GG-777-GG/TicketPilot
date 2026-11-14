"""文件任务迁移回归：仅操作临时 SQLite，不修改用户数据库。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


PREVIOUS = "2d9a3f0abac4"
CURRENT = "9606ce5dd746"


@pytest.fixture
def migrated_jobs(tmp_path: Path):
    # 为每道题创建独立数据库，先存一条旧版文本任务再升级。
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    url = f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}"
    config.attributes["database_url"] = url
    command.upgrade(config, PREVIOUS)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (id,email,display_name,password_hash) "
                "VALUES ('owner','owner@example.com','Owner','hash')"
            ))
            connection.execute(text(
                "INSERT INTO knowledge_ingestion_jobs "
                "(id,title,raw_content,source_type,visibility,requested_by,created_at,updated_at) "
                "VALUES ('legacy','Legacy','keep this content','manual','internal','owner',"
                "'2026-10-24 00:00:00','2026-10-24 00:00:00')"
            ))
        command.upgrade(config, CURRENT)
        yield config, engine
    finally:
        engine.dispose()


def test_upgrade_and_text_only_downgrade_preserve_old_job(migrated_jobs):
    config, engine = migrated_jobs
    columns = {c["name"]: c for c in inspect(engine).get_columns("knowledge_ingestion_jobs")}
    assert columns["raw_content"]["nullable"] is True
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT raw_content,storage_key,original_filename "
            "FROM knowledge_ingestion_jobs WHERE id='legacy'"
        )).one() == ("keep this content", None, None)
    command.downgrade(config, PREVIOUS)
    columns = {c["name"]: c for c in inspect(engine).get_columns("knowledge_ingestion_jobs")}
    assert "storage_key" not in columns
    assert "original_filename" not in columns
    assert columns["raw_content"]["nullable"] is False
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT raw_content FROM knowledge_ingestion_jobs WHERE id='legacy'"
        )) == "keep this content"


@pytest.mark.parametrize(
    ("body", "key", "filename", "source_type", "allowed"),
    [
        ("text", None, None, "manual", True),
        ("parsed file text", None, None, "file", True),
        (None, "a" * 32 + ".bin", "contract.pdf", "file", True),
        (None, None, None, "manual", False),
        (None, "a" * 32 + ".bin", None, "file", False),
        (None, None, "contract.pdf", "file", False),
        ("text", "a" * 32 + ".bin", "contract.pdf", "file", False),
        (None, "a" * 32 + ".bin", "contract.pdf", "manual", False),
    ],
)
def test_migrated_input_constraint(
    migrated_jobs,
    body,  # 文本输入；文件任务为空。
    key,  # 内部文件标识。
    filename,  # 原始文件名。
    source_type,  # 来源类型。
    allowed,  # 人工标注是否应被数据库接受。
):
    _, engine = migrated_jobs
    statement = text(
        "INSERT INTO knowledge_ingestion_jobs "
        "(id,title,raw_content,storage_key,original_filename,source_type,visibility,requested_by,created_at,updated_at) "
        "VALUES ('new','New',:body,:key,:filename,:source_type,'internal','owner',"
        "'2026-10-24 00:00:00','2026-10-24 00:00:00')"
    )
    values = dict(body=body, key=key, filename=filename, source_type=source_type)
    if allowed:
        with engine.begin() as connection:
            connection.execute(statement, values)
    else:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(statement, values)


def test_file_job_blocks_downgrade_before_schema_changes(migrated_jobs):
    config, engine = migrated_jobs
    key = "b" * 32 + ".bin"
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO knowledge_ingestion_jobs "
            "(id,title,raw_content,storage_key,original_filename,source_type,visibility,requested_by,created_at,updated_at) "
            "VALUES ('file','File',NULL,:key,'contract.pdf','file','internal','owner',"
            "'2026-10-24 00:00:00','2026-10-24 00:00:00')"
        ), {"key": key})
    with pytest.raises(RuntimeError, match="无法回滚"):
        command.downgrade(config, PREVIOUS)
    # 拒绝回滚后，数据、约束和迁移版本必须仍然保留。
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT
        assert connection.execute(text(
            "SELECT storage_key,original_filename FROM knowledge_ingestion_jobs WHERE id='file'"
        )).one() == (key, "contract.pdf")
    constraints = inspect(engine).get_check_constraints("knowledge_ingestion_jobs")
    assert "ck_knowledge_ingestion_jobs_input" in {c["name"] for c in constraints}
