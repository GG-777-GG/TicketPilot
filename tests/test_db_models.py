from sqlalchemy import (
    create_engine,
    select,
)
from sqlalchemy.orm import Session

from ticketpilot.database import Base
# 导入工单和用户 ORM 模型
from ticketpilot.db_models import (
    TicketRecord,
    UserRecord,
)


# 验证 ORM 模型可以保存并查询工单
def test_ticket_record_can_be_saved_and_loaded() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
    )
    Base.metadata.create_all(
        bind=engine,
    )

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

    with Session(engine) as session:
        session.add(ticket)
        session.commit()

        statement = select(TicketRecord).where(
            TicketRecord.id == "T-001",
        )
        loaded_ticket = session.scalar(statement)

        assert loaded_ticket is not None
        assert loaded_ticket.id == "T-001"
        assert loaded_ticket.customer_name == "GG"
        assert loaded_ticket.status == "open"

# 验证用户 ORM 模型可以保存、查询并取得默认值
def test_user_record_can_be_saved_with_defaults() -> None:
    # 创建本测试独享的内存 SQLite 数据库
    test_engine = create_engine(
        "sqlite:///:memory:",
    )

    # 根据全部 ORM 模型创建数据库表
    Base.metadata.create_all(
        bind=test_engine,
    )

    # 创建一名尚未保存的用户
    user = UserRecord(
        # 设置用户唯一 ID
        id="U-001",

        # 设置登录邮箱
        email="agent@example.com",

        # 设置用户显示名称
        display_name="测试客服",

        # 数据库只保存密码哈希
        # 本测试不负责验证这个哈希是否真实
        password_hash="argon2-hash-for-model-test",
    )

    # 使用第一个 Session 保存用户
    with Session(test_engine) as session:
        # 将用户加入 Session
        session.add(user)

        # 提交事务，执行 INSERT
        session.commit()

    # 使用新的 Session 重新查询
    # 证明数据确实已经写入数据库
    with Session(test_engine) as session:
        # 根据主键查询用户
        loaded_user = session.get(
            UserRecord,
            "U-001",
        )

        # 用户必须存在
        assert loaded_user is not None

        # 验证用户 ID
        assert loaded_user.id == "U-001"

        # 验证登录邮箱
        assert (
            loaded_user.email
            == "agent@example.com"
        )

        # 验证显示名称
        assert (
            loaded_user.display_name
            == "测试客服"
        )

        # 验证密码字段保存的是传入的哈希
        assert (
            loaded_user.password_hash
            == "argon2-hash-for-model-test"
        )

        # 没有指定角色时应该自动成为 agent
        assert loaded_user.role == "agent"

        # 新账号默认应该处于启用状态
        assert loaded_user.is_active is True

        # 数据库应该自动生成账号创建时间
        assert loaded_user.created_at is not None

    # 释放测试数据库连接
    test_engine.dispose()