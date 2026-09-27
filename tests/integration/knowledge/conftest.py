"""
提供知识库 PostgreSQL 集成测试专用的 fixture。

正常流程：
读取开发数据库连接配置
→ 把数据库名称替换成 ticketpilot_test
→ 确认没有连接开发数据库
→ 启用 vector 扩展
→ 创建测试需要的表
→ 提供 PostgreSQL Session
→ 测试结束后删除测试表并释放连接
"""

from collections.abc import Generator

import pytest
from sqlalchemy import (
    create_engine,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)

# 导入统一的 ORM Base。
from ticketpilot.core.database import Base

# 导入当前项目的数据库配置。
from ticketpilot.core.config import settings

# 导入 ORM 模型，把表登记到 Base.metadata。
import ticketpilot.identity.models  # noqa: F401, E402
import ticketpilot.knowledge.models  # noqa: F401, E402


@pytest.fixture
# 这个 fixture 负责提供真实的 PostgreSQL 测试 Session。
def postgres_session() -> Generator[
    Session,
    None,
    None,
]:
    # 把配置中的数据库地址解析成 SQLAlchemy URL 对象。
    development_url = make_url(
        settings.database_url
    )

    # 真实 pgvector 测试必须使用 PostgreSQL。
    if (
        development_url.get_backend_name()
        != "postgresql"
    ):
        pytest.skip(
            "当前没有配置 PostgreSQL 数据库"
        )

    # 沿用相同的地址、端口、用户名和密码，
    # 但把数据库名称替换成专用测试数据库。
    test_database_url = development_url.set(
        database="ticketpilot_test"
    )

    # 防止配置错误时误删开发数据库中的表。
    if (
        test_database_url.database
        != "ticketpilot_test"
    ):
        raise RuntimeError(
            "PostgreSQL 集成测试只能使用 "
            "ticketpilot_test 数据库"
        )

    # 为测试数据库创建独立的 SQLAlchemy Engine。
    test_engine = create_engine(
        test_database_url
    )

    try:
        # 确保测试数据库已经启用 pgvector。
        with test_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE EXTENSION "
                    "IF NOT EXISTS vector"
                )
            )

        # 根据当前登记的 ORM 模型创建测试表。
        Base.metadata.create_all(
            bind=test_engine
        )

        # 创建测试专用的 Session 工厂。
        TestSessionLocal = sessionmaker(
            bind=test_engine,
            autoflush=False,
            expire_on_commit=False,
        )

        # 当前测试得到一个独立 Session。
        session = TestSessionLocal()

        try:
            yield session

        finally:
            # 测试结束时撤销尚未提交的事务。
            session.rollback()

            # 归还当前数据库连接。
            session.close()

    finally:
        # 这里只会删除 ticketpilot_test 中的测试表。
        Base.metadata.drop_all(
            bind=test_engine
        )

        # 释放 Engine 持有的连接池。
        test_engine.dispose()