# 导入 Path，用于定位项目根目录和 alembic.ini
from pathlib import Path

# command 提供 upgrade()、downgrade() 等 Alembic 操作
from alembic import command

# Config 用于读取并控制 alembic.ini 配置
from alembic.config import Config

# ScriptDirectory 用于读取迁移脚本和最新版本号
from alembic.script import ScriptDirectory

# 导入数据库创建、检查和执行 SQL 所需工具
from sqlalchemy import (
    create_engine,
    inspect,
    text,
)


# 根据当前测试文件的位置得到项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# 验证 Alembic 可以升级和回滚一个全新的临时数据库
def test_alembic_upgrade_and_downgrade(
        # pytest 提供独立的临时目录
        tmp_path,
) -> None:
    # 在临时目录中指定数据库文件
    database_file = tmp_path / "migration-test.db"

    # 将临时文件路径转换成 SQLite 数据库地址
    database_url = (
        f"sqlite:///{database_file.as_posix()}"
    )

    # 读取项目根目录中的 Alembic 配置
    alembic_config = Config(
        str(PROJECT_ROOT / "alembic.ini")
    )

    # 把临时数据库地址传给 migrations/env.py
    alembic_config.attributes[
        "database_url"
    ] = database_url

    # 将空数据库升级到最新迁移版本
    command.upgrade(
        alembic_config,
        "head",
    )

    # 创建连接临时数据库的 SQLAlchemy Engine
    test_engine = create_engine(
        database_url
    )

    # 使用 try 保证测试结束后释放数据库连接
    try:
        # 查询升级后的全部表名
        table_names = inspect(
            test_engine
        ).get_table_names()

        # 验证迁移创建了业务表
        assert "tickets" in table_names

        # 验证 Alembic 创建了版本记录表
        assert "alembic_version" in table_names

        # 打开数据库连接，读取当前迁移版本
        with test_engine.connect() as connection:
            # 查询 alembic_version 表中的版本号
            current_revision = connection.execute(
                text(
                    "SELECT version_num "
                    "FROM alembic_version"
                )
            ).scalar_one()

        # 读取 migrations/versions 中的迁移关系
        migration_scripts = (
            ScriptDirectory.from_config(
                alembic_config
            )
        )

        # 验证数据库版本就是最新迁移版本
        assert current_revision == (
            migration_scripts.get_current_head()
        )

        # 将临时数据库回滚到第一份迁移之前
        command.downgrade(
            alembic_config,
            "base",
        )

        # 重新查询回滚后的表名
        remaining_table_names = inspect(
            test_engine
        ).get_table_names()

        # 验证 downgrade() 已经删除 tickets 表
        assert "tickets" not in remaining_table_names

    # 无论测试成功还是失败，都执行清理
    finally:
        # 释放测试数据库连接
        test_engine.dispose()