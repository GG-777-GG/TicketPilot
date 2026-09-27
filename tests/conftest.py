"""
提供多个集成测试共同使用的 pytest fixture。

职责边界：
- fixture 负责创建和释放临时数据库基础设施。
- factory 只构造对象，不执行数据库操作。
- 测试自己决定何时 commit 或 rollback。
"""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests.factories.identity import build_user
from ticketpilot.core.database import Base
from ticketpilot.identity.models import UserRecord

# 导入各业务模块 ORM，确保所有表都登记到 Base.metadata。
import ticketpilot.knowledge.models  # noqa: F401, E402
import ticketpilot.tickets.models  # noqa: F401, E402


@pytest.fixture
def database_session() -> Generator[Session, None, None]:
    """为单项测试提供启用外键约束的内存 SQLite Session。"""

    test_engine = create_engine(
        "sqlite://",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    @event.listens_for(
        test_engine,
        "connect",
    )
    def enable_sqlite_foreign_keys(
        dbapi_connection,
        _connection_record,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=test_engine)

    TestSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    session = TestSessionLocal()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


@pytest.fixture
def database_user(
    database_session: Session,
) -> UserRecord:
    """创建并 flush 测试用户，但把 commit 权交给具体测试。"""

    user = build_user()
    database_session.add(user)
    database_session.flush()
    return user
