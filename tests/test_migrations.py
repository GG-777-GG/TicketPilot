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

# 验证增加 assigned_to 字段后，旧工单的数据仍然完整
def test_assigned_to_migration_preserves_existing_ticket(
        # pytest 提供独立临时目录，所有操作都在测试数据库中执行
        tmp_path,
) -> None:
    # 指定本次测试使用的数据库文件
    database_file = tmp_path / "existing-ticket-test.db"

    # 将文件路径转换成 SQLite 数据库地址
    database_url = f"sqlite:///{database_file.as_posix()}"

    # 读取项目的 Alembic 配置
    alembic_config = Config(
        str(PROJECT_ROOT / "alembic.ini")
    )

    # 让 env.py 使用临时数据库地址
    alembic_config.attributes["database_url"] = database_url

    # 创建连接临时数据库的 Engine
    test_engine = create_engine(database_url)

    # 无论后面的迁移或断言是否成功，最终都要释放连接
    try:
        # 先升级到第一版，此时 tickets 表还没有 assigned_to
        command.upgrade(
            alembic_config,
            "3807ef3dc440",
        )

        # 准备一张符合旧表结构的工单，供插入和后续核对使用
        old_ticket = {
            # 工单编号
            "id": "T-OLD-001",
            # 客户姓名
            "customer_name": "GG",
            # 联系邮箱
            "contact_email": "gg@example.com",
            # 来源渠道
            "channel": "web",
            # 工单标题
            "subject": "升级前创建的工单",
            # 问题描述
            "description": "升级后这段内容应该仍然存在",
            # 优先级
            "priority": "high",
            # 工单状态
            "status": "open",
        }

        # 开启事务，退出这个 with 代码块时自动提交
        with test_engine.begin() as connection:
            # INSERT INTO 指定要写入的八个旧字段
            # VALUES 中的 :字段名从 old_ticket 字典取得对应值
            connection.execute(
                text(
                    """
                    INSERT INTO tickets (
                        id, customer_name, contact_email, channel,
                        subject, description, priority, status
                    )
                    VALUES (
                        :id, :customer_name, :contact_email, :channel,
                        :subject, :description, :priority, :status
                    )
                    """
                ),
                # 将工单字典作为 SQL 参数传入
                old_ticket,
            )

        # 旧工单已经提交，现在执行增加 assigned_to 的迁移
        command.upgrade(
            alembic_config,
            "9782abec9f36",
        )

        # 打开连接，查询升级后的旧工单
        with test_engine.connect() as connection:
            # 根据原来的工单编号，查询该工单的全部字段
            result = connection.execute(
                text(
                    "SELECT * FROM tickets WHERE id = :id"
                ),
                # 向 SQL 中的 :id 占位符提供值
                {"id": old_ticket["id"]},
            )

            # 取得恰好一行结果，并允许按字段名读取
            saved_ticket = result.mappings().one()

        # 遍历升级前的每个字段及其原始值
        for field_name, original_value in old_ticket.items():
            # 验证数据库里的旧字段值没有被迁移改变
            assert saved_ticket[field_name] == original_value

        # 验证新字段已经存在，旧工单尚未分配处理人
        assert saved_ticket["assigned_to"] is None

    # 即使测试失败，也执行资源清理
    finally:
        # 释放测试 Engine 的数据库连接
        test_engine.dispose()

# 验证时间字段迁移能够为旧工单补上创建时间和更新时间
def test_timestamp_migration_backfills_existing_ticket(
    tmp_path,
) -> None:
    # 在 pytest 的临时目录中创建一个测试数据库
    # 不会影响 Docker 中真正的 PostgreSQL 数据库
    database_file = tmp_path / "timestamp-migration-test.db"

    # 生成这个临时 SQLite 数据库的连接地址
    database_url = f"sqlite:///{database_file.as_posix()}"

    # 读取项目中的 Alembic 配置文件
    alembic_config = Config(
        str(PROJECT_ROOT / "alembic.ini")
    )

    # 让本次测试使用临时数据库
    alembic_config.attributes["database_url"] = database_url

    # 创建测试数据库的 Engine
    test_engine = create_engine(database_url)

    try:
        # 先把数据库升级到“还没有时间字段”的旧版本
        command.upgrade(
            alembic_config,
            "9782abec9f36",
        )

        # 模拟数据库中已经存在的一张旧工单
        # 这里故意不提供 created_at 和 updated_at
        old_ticket = {
            "id": "T-TIME-001",
            "customer_name": "GG",
            "contact_email": "gg@example.com",
            "channel": "web",
            "subject": "时间字段迁移测试",
            "description": "升级后旧数据和时间字段都应该正确",
            "priority": "high",
            "status": "open",
            "assigned_to": "张三",
        }

        # 开启一个数据库事务
        with test_engine.begin() as connection:
            # 把旧工单直接插入旧版本的 tickets 表
            connection.execute(
                text(
                    """
                    INSERT INTO tickets (
                        id,
                        customer_name,
                        contact_email,
                        channel,
                        subject,
                        description,
                        priority,
                        status,
                        assigned_to
                    )
                    VALUES (
                        :id,
                        :customer_name,
                        :contact_email,
                        :channel,
                        :subject,
                        :description,
                        :priority,
                        :status,
                        :assigned_to
                    )
                    """
                ),
                old_ticket,
            )

        # 执行我们刚刚编写的时间字段迁移
        command.upgrade(
            alembic_config,
            "1546b4e6b921",
        )

        # 查询升级后的旧工单
        with test_engine.connect() as connection:
            result = connection.execute(
                text(
                    """
                    SELECT *
                    FROM tickets
                    WHERE id = :id
                    """
                ),
                {
                    "id": old_ticket["id"],
                },
            )

            # one() 表示这里必须刚好查到一条记录
            saved_ticket = result.mappings().one()

        # 检查旧工单原来的数据没有被迁移破坏
        for field_name, original_value in old_ticket.items():
            assert saved_ticket[field_name] == original_value

        # 检查迁移为旧工单补上了创建时间
        assert saved_ticket["created_at"] is not None

        # 检查迁移为旧工单补上了更新时间
        assert saved_ticket["updated_at"] is not None

        # 旧数据迁移时，两个时间应该使用相同的初始时间
        assert saved_ticket["updated_at"] == saved_ticket["created_at"]

        # 读取升级后 tickets 表的字段结构
        columns = {
            column["name"]: column
            for column in inspect(test_engine).get_columns("tickets")
        }

        # 检查两个时间字段最终都不允许为 NULL
        assert columns["created_at"]["nullable"] is False
        assert columns["updated_at"]["nullable"] is False

    finally:
        # 无论测试通过还是失败，都释放数据库连接
        test_engine.dispose()

# 验证用户表迁移会创建正确的表结构
def test_users_migration_creates_expected_table(
    # pytest 提供本测试独享的临时目录
    tmp_path,
) -> None:
    # 创建本次测试使用的临时 SQLite 文件
    database_file = (
        tmp_path / "users-migration-test.db"
    )

    # 生成临时数据库连接地址
    database_url = (
        f"sqlite:///{database_file.as_posix()}"
    )

    # 读取项目的 Alembic 配置文件
    alembic_config = Config(
        str(PROJECT_ROOT / "alembic.ini")
    )

    # 让 Alembic 使用本次测试的临时数据库
    alembic_config.attributes["database_url"] = (
        database_url
    )

    # 创建用于检查数据库结构的 Engine
    test_engine = create_engine(
        database_url
    )

    try:
        # 先升级到还没有 users 表的旧版本
        command.upgrade(
            alembic_config,
            "1546b4e6b921",
        )

        # 验证旧版本中还没有 users 表
        assert "users" not in inspect(
            test_engine
        ).get_table_names()

        # 执行创建 users 表的新迁移
        command.upgrade(
            alembic_config,
            "d730dcf4c7e6",
        )

        # 创建数据库结构检查器
        database_inspector = inspect(
            test_engine
        )

        # 读取升级后的全部表名
        table_names = (
            database_inspector.get_table_names()
        )

        # 原来的 tickets 表必须继续存在
        assert "tickets" in table_names

        # 新的 users 表必须已经创建
        assert "users" in table_names

        # 读取 users 表的全部字段
        columns = {
            column["name"]: column
            for column in database_inspector.get_columns(
                "users"
            )
        }

        # 验证用户表拥有预期的全部字段
        assert set(columns) == {
            "id",
            "email",
            "display_name",
            "password_hash",
            "role",
            "is_active",
            "created_at",
        }

        # 所有用户字段都不允许保存 NULL
        for column in columns.values():
            assert column["nullable"] is False

        # role 必须有数据库默认值
        assert columns["role"]["default"] is not None

        # is_active 必须有数据库默认值
        assert (
            columns["is_active"]["default"]
            is not None
        )

        # created_at 必须有数据库默认值
        assert (
            columns["created_at"]["default"]
            is not None
        )

        # 读取 users 表的唯一约束
        unique_constraints = (
            database_inspector.get_unique_constraints(
                "users"
            )
        )

        # 验证 email 字段具有唯一约束
        assert any(
            constraint["column_names"] == ["email"]
            for constraint in unique_constraints
        )

        # 回滚到没有 users 表的旧版本
        command.downgrade(
            alembic_config,
            "1546b4e6b921",
        )

        # 重新读取回滚后的表名
        tables_after_downgrade = inspect(
            test_engine
        ).get_table_names()

        # users 表应该已经删除
        assert "users" not in tables_after_downgrade

        # 回滚用户表不能删除原来的 tickets 表
        assert "tickets" in tables_after_downgrade

    finally:
        # 无论测试成功或失败都释放数据库连接
        test_engine.dispose()