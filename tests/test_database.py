from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ticketpilot.database import (
    build_engine,
    build_session_factory,
    create_tables,
)
from ticketpilot.db_models import TicketRecord


# 验证数据库配置可以创建文件、表和可用的 Session
def test_database_configuration_creates_working_session(
        tmp_path,
) -> None:
    database_file = tmp_path / "test.db"
    database_url = (
        f"sqlite:///{database_file.as_posix()}"
    )

    test_engine = build_engine(database_url)
    test_session_factory = build_session_factory(
        test_engine,
    )

    create_tables(test_engine)

    ticket = TicketRecord(
        id="T-001",
        customer_name="GG",
        contact_email="gg@example.com",
        channel="web",
        subject="无法登录",
        description="输入密码后没有反应",
        priority="high",
        status="open",
    )

    with test_session_factory() as session:
        session.add(ticket)
        session.commit()

    with test_session_factory() as session:
        loaded_ticket = session.get(
            TicketRecord,
            "T-001",
        )

        assert loaded_ticket is not None
        assert loaded_ticket.subject == "无法登录"

    table_names = inspect(
        test_engine,
    ).get_table_names()

    assert database_file.exists()
    assert "tickets" in table_names

    test_engine.dispose()


# 验证生成器依赖最终一定会关闭 Session
def test_get_session_closes_session(
        # 接收 pytest 提供的临时替换工具
        monkeypatch,
) -> None:
    # 定义一个最小的假 Session，用来记录 close() 是否执行
    class FakeSession:
        # 创建假 Session 时默认还没有关闭
        def __init__(self) -> None:
            # 保存关闭状态
            self.closed = False

        # 模拟真实 Session 的 close() 方法
        def close(self) -> None:
            # 记录 close() 已经被调用
            self.closed = True

    # 创建一个假 Session 实例
    fake_session = FakeSession()

    # 定义假的 Session 工厂
    def fake_session_factory() -> FakeSession:
        # 每次调用工厂都返回上面创建的假 Session
        return fake_session

    # 临时导入 database 模块，以便替换模块中的 SessionLocal
    import ticketpilot.database as database

    # 把真实 SessionLocal 临时替换成假工厂
    monkeypatch.setattr(
        # 指定需要修改的模块
        database,
        # 指定模块中需要替换的名字
        "SessionLocal",
        # 指定替换后的假工厂
        fake_session_factory,
    )

    # 调用 get_session()，得到生成器对象
    session_generator = database.get_session()

    # 让生成器运行到 yield，并取得它提供的 Session
    yielded_session = next(
        session_generator
    )

    # 验证 yield 出来的正是我们准备的假 Session
    assert yielded_session is fake_session

    # yield 暂停期间，Session 还不应该关闭
    assert fake_session.closed is False

    # 关闭生成器，使 finally 部分开始执行
    session_generator.close()

    # 验证 finally 已经调用 Session.close()
    assert fake_session.closed is True
