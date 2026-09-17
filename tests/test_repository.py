# 导入 pytest，用来验证预期异常
import pytest

# 导入 datetime，用来设置一个固定的旧时间
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError

# 导入数据库配置构造函数
from ticketpilot.core.database import (
    build_engine,
    build_session_factory,
    create_tables,
)

# 导入数据库工单模型
from ticketpilot.tickets.models import TicketRecord

# 导入本批次需要测试的三个 Repository 函数
from ticketpilot.tickets.repository import (
    # 导入创建工单函数
    create_ticket,
    # 导入删除工单函数
    delete_ticket,
    # 导入按 ID 查询函数
    get_ticket_by_id,
    # 导入列表查询函数
    list_tickets,
    # 导入状态修改函数
    update_ticket_status,
    # 导入处理人修改函数
    update_ticket_assignee,
)


# 创建一张供 Repository 测试使用的 ORM 工单
def make_ticket_record(
    # 允许测试为不同工单指定不同 ID
    ticket_id: str = "T-001",
# 返回一个尚未保存的 TicketRecord 对象
) -> TicketRecord:
    # 创建并返回完整的 ORM 工单对象
    return TicketRecord(
        # 使用调用者传入的工单 ID
        id=ticket_id,
        # 设置客户姓名
        customer_name="GG",
        # 设置联系邮箱
        contact_email="gg@example.com",
        # 设置工单来源渠道
        channel="web",
        # 设置工单标题
        subject="无法登录",
        # 设置详细问题描述
        description="输入密码后没有反应",
        # 设置优先级
        priority="high",
        # 设置初始状态
        status="open",
    )


# 验证 Repository 可以创建、列表查询和主键查询工单
def test_repository_creates_and_reads_tickets() -> None:
    # 创建只存在于当前测试中的内存 SQLite Engine
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 根据 ORM 模型在测试数据库中创建 tickets 表
    create_tables(test_engine)

    # 创建绑定测试 Engine 的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 通过 Session 工厂创建并自动关闭一次 Session
    with test_session_factory() as session:
        # 创建第一张尚未保存的 ORM 工单
        first_ticket = make_ticket_record(
            "T-001",
        )

        # 创建第二张尚未保存的 ORM 工单
        second_ticket = make_ticket_record(
            "T-002",
        )

        # 给第一张工单设置固定的较早创建时间
        first_ticket.created_at = datetime(
            2026,
            1,
            1,
        )

        # 设置第一张工单的初始更新时间
        first_ticket.updated_at = (
            first_ticket.created_at
        )

        # 给第二张工单设置固定的较晚创建时间
        second_ticket.created_at = datetime(
            2026,
            1,
            2,
        )

        # 设置第二张工单的初始更新时间
        second_ticket.updated_at = (
            second_ticket.created_at
        )

        # 调用 Repository 保存第一张工单
        created_first_ticket = create_ticket(
            session,
            first_ticket,
        )

        # 调用 Repository 保存第二张工单
        create_ticket(
            session,
            second_ticket,
        )

        # 调用 Repository 查询全部工单
        all_tickets = list_tickets(session)

        # 根据存在的主键查询第一张工单
        found_ticket = get_ticket_by_id(
            session,
            "T-001",
        )

        # 根据不存在的主键执行查询
        missing_ticket = get_ticket_by_id(
            session,
            "T-999",
        )

        # 验证创建操作返回了第一张工单
        assert created_first_ticket.id == "T-001"

        # 验证数据库自动生成了创建时间
        assert created_first_ticket.created_at is not None

        # 验证数据库自动生成了更新时间
        assert created_first_ticket.updated_at is not None

        # 新建工单时，创建时间和更新时间应该相同
        assert (
                created_first_ticket.created_at
                == created_first_ticket.updated_at
        )

        # 验证列表查询得到了两张工单
        assert len(all_tickets) == 2

        # T-002 创建时间较晚，因此应该排在第一位
        assert all_tickets[0].id == "T-002"

        # T-001 创建时间较早，因此应该排在第二位
        assert all_tickets[1].id == "T-001"

        # 验证存在的工单能够被找到
        assert found_ticket is not None

        # 验证找到的是目标工单
        assert found_ticket.id == "T-001"

        # 验证不存在的工单返回 None
        assert missing_ticket is None

    # 关闭 Engine 持有的数据库连接
    test_engine.dispose()


# 验证底层数据库错误会被转换成 RuntimeError
def test_repository_converts_database_error() -> None:
    # 创建一个新的空内存数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 创建绑定测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 故意不调用 create_tables()，因此 tickets 表不存在

    # 创建并自动关闭测试 Session
    with test_session_factory() as session:
        # 声明这里应该出现 RuntimeError
        with pytest.raises(
            # 验证异常类型
            RuntimeError,
            # 验证异常消息，确认是 Repository 转换后的异常
            match="查询工单列表失败",
        ):
            # 查询不存在的 tickets 表，触发数据库错误
            list_tickets(session)

    # 释放 Engine 持有的数据库连接
    test_engine.dispose()

# 验证 Repository 可以持久化状态修改和删除操作
def test_repository_updates_and_deletes_tickets() -> None:
    # 创建本测试独享的内存 SQLite 数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 在测试数据库中创建 tickets 表
    create_tables(test_engine)

    # 创建绑定测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 创建第一个数据库 Session
    with test_session_factory() as session:
        # 创建准备修改的第一张工单
        first_ticket = make_ticket_record(
            "T-001",
        )

        # 创建准备删除的第二张工单
        second_ticket = make_ticket_record(
            "T-002",
        )

        # 保存第一张工单
        create_ticket(
            session,
            first_ticket,
        )

        # 保存第二张工单
        create_ticket(
            session,
            second_ticket,
        )

        # 把第一张工单状态修改为 resolved
        updated_ticket = update_ticket_status(
            session,
            first_ticket,
            "resolved",
        )

        # 删除第二张工单
        delete_ticket(
            session,
            second_ticket,
        )

        # 验证函数返回的对象状态已经修改
        assert updated_ticket.status == "resolved"

    # 使用新的 Session 验证修改确实写入了数据库
    with test_session_factory() as session:
        # 重新查询第一张工单
        stored_first_ticket = get_ticket_by_id(
            session,
            "T-001",
        )

        # 重新查询已经删除的第二张工单
        stored_second_ticket = get_ticket_by_id(
            session,
            "T-002",
        )

        # 第一张工单应该仍然存在
        assert stored_first_ticket is not None

        # 第一张工单的新状态应该已被持久化
        assert stored_first_ticket.status == "resolved"

        # 第二张工单应该已经不存在
        assert stored_second_ticket is None

    # 释放 Engine 管理的数据库连接
    test_engine.dispose()


# 验证修改状态提交失败时数据库会回滚
def test_update_ticket_status_rolls_back(
    # 接收 pytest 提供的临时替换工具
    monkeypatch,
) -> None:
    # 创建本测试独享的内存数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 创建数据库表
    create_tables(test_engine)

    # 创建测试 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 创建测试 Session
    with test_session_factory() as session:
        # 创建一张初始状态为 open 的工单
        ticket = make_ticket_record(
            "T-001",
        )

        # 先正常保存这张工单
        create_ticket(
            session,
            ticket,
        )

        # 定义一个模拟提交失败的函数
        def fake_commit() -> None:
            # 主动抛出 SQLAlchemy 数据库异常
            raise SQLAlchemyError(
                "模拟数据库提交失败"
            )

        # 临时把当前 Session 的 commit 替换为失败函数
        monkeypatch.setattr(
            session,
            "commit",
            fake_commit,
        )

        # 声明状态修改应该转换成 RuntimeError
        with pytest.raises(
            # 验证异常类型
            RuntimeError,
            # 验证异常来自状态修改 Repository
            match="修改工单状态失败",
        ):
            # 尝试把状态改成 resolved
            update_ticket_status(
                session,
                ticket,
                "resolved",
            )

        # 回滚后重新根据主键取得工单
        rolled_back_ticket = session.get(
            TicketRecord,
            "T-001",
        )

        # 工单应该仍然存在
        assert rolled_back_ticket is not None

        # 状态应该恢复为事务开始前的 open
        assert rolled_back_ticket.status == "open"

    # 释放数据库连接
    test_engine.dispose()

# 验证删除提交失败时工单仍然存在
def test_delete_ticket_rolls_back(
    # 接收 pytest 的临时替换工具
    monkeypatch,
) -> None:
    # 创建本测试独享的内存数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 创建数据库表
    create_tables(test_engine)

    # 创建绑定测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 创建测试 Session
    with test_session_factory() as session:
        # 创建准备删除的工单
        ticket = make_ticket_record(
            "T-001",
        )

        # 先把工单正常保存到数据库
        create_ticket(
            session,
            ticket,
        )

        # 定义模拟提交失败的函数
        def fake_commit() -> None:
            # 主动产生 SQLAlchemy 数据库异常
            raise SQLAlchemyError(
                "模拟数据库提交失败"
            )

        # 将当前 Session 的 commit 临时替换为失败函数
        monkeypatch.setattr(
            session,
            "commit",
            fake_commit,
        )

        # 声明删除函数应该抛出转换后的 RuntimeError
        with pytest.raises(
            # 验证异常类型
            RuntimeError,
            # 验证异常消息
            match="删除工单失败",
        ):
            # 尝试删除工单
            delete_ticket(
                session,
                ticket,
            )

        # 回滚后重新查询相同主键
        rolled_back_ticket = session.get(
            TicketRecord,
            "T-001",
        )

        # 删除没有提交成功，所以工单必须仍然存在
        assert rolled_back_ticket is not None

        # 验证恢复的是原来的工单
        assert rolled_back_ticket.id == "T-001"

    # 释放 Engine 管理的连接
    test_engine.dispose()

# 验证 Repository 可以分配并取消分配工单处理人
def test_update_ticket_assignee_persists_and_clears() -> None:
    # 创建本测试独享的内存数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 根据 ORM 模型创建数据库表
    create_tables(test_engine)

    # 创建绑定测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 使用第一个 Session 创建工单并分配处理人
    with test_session_factory() as session:
        # 创建一张尚未保存的工单
        ticket = make_ticket_record(
            "T-001",
        )

        # 先将工单保存到数据库
        create_ticket(
            session,
            ticket,
        )

        # 将工单分配给张三
        assigned_ticket = update_ticket_assignee(
            session,
            ticket,
            "张三",
        )

        # 函数应该返回传入的同一个 ORM 对象
        assert assigned_ticket is ticket
        # 返回对象应该包含新的处理人
        assert assigned_ticket.assigned_to == "张三"

    # 使用新的 Session 验证处理人确实保存到了数据库
    with test_session_factory() as session:
        # 根据主键重新查询工单
        stored_ticket = session.get(
            TicketRecord,
            "T-001",
        )

        # 工单必须仍然存在
        assert stored_ticket is not None

        # 新 Session 也能查询到处理人，证明已经持久化
        assert stored_ticket.assigned_to == "张三"

        # 传入 None，取消分配处理人
        update_ticket_assignee(
            session,
            stored_ticket,
            None,
        )

    # 再使用一个新 Session 验证 NULL 已经保存
    with test_session_factory() as session:
        # 重新查询工单
        cleared_ticket = session.get(
            TicketRecord,
            "T-001",
        )

        # 工单本身没有被删除
        assert cleared_ticket is not None

        # 处理人已经被取消
        assert cleared_ticket.assigned_to is None

    # 释放数据库连接
    test_engine.dispose()

# 验证修改处理人提交失败时会恢复原来的值
def test_update_ticket_assignee_rolls_back(
        # pytest 提供临时替换工具
        monkeypatch,
) -> None:
    # 创建本测试独享的内存数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 创建数据库表
    create_tables(test_engine)

    # 创建绑定测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 创建本测试使用的 Session
    with test_session_factory() as session:
        # 创建一张工单
        ticket = make_ticket_record(
            "T-001",
        )

        # 设置已经存在的处理人
        ticket.assigned_to = "张三"

        # 正常保存初始数据
        create_ticket(
            session,
            ticket,
        )

        # 定义一个模拟提交失败的函数
        def fake_commit() -> None:
            # 模拟数据库提交异常
            raise SQLAlchemyError(
                "模拟数据库提交失败"
            )

        # 临时将当前 Session 的 commit 替换成失败函数
        monkeypatch.setattr(
            session,
            "commit",
            fake_commit,
        )

        # 验证数据库异常会被转换成项目异常
        with pytest.raises(
            RuntimeError,
            match="修改工单处理人失败",
        ):
            # 尝试把处理人从张三改成李四
            update_ticket_assignee(
                session,
                ticket,
                "李四",
            )

        # rollback 后访问属性时，SQLAlchemy 会重新读取数据库
        # 因此处理人应该恢复为提交前的张三
        assert ticket.assigned_to == "张三"

    # 释放数据库连接
    test_engine.dispose()

# 验证修改工单状态时会刷新 updated_at
def test_update_ticket_status_refreshes_updated_at() -> None:
    # 创建本测试独享的内存 SQLite 数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 根据 ORM 模型创建 tickets 表
    create_tables(test_engine)

    # 创建绑定测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 创建并自动关闭测试 Session
    with test_session_factory() as session:
        # 创建一张尚未保存的工单
        ticket = make_ticket_record(
            "T-TIME-001",
        )

        # 保存工单，让数据库生成初始时间
        created_ticket = create_ticket(
            session,
            ticket,
        )

        # 保存最初的创建时间
        original_created_at = (
            created_ticket.created_at
        )

        # 手动把更新时间设置成一个很早的时间
        # 这样不需要使用 sleep 等待时间变化
        created_ticket.updated_at = datetime(
            2000,
            1,
            1,
        )

        # 把这个旧时间保存到测试数据库
        session.commit()

        # 记录状态修改前的更新时间
        old_updated_at = (
            created_ticket.updated_at
        )

        # 修改工单状态
        updated_ticket = update_ticket_status(
            session,
            created_ticket,
            "resolved",
        )

        # 验证业务字段已经修改成功
        assert updated_ticket.status == "resolved"

        # 修改工单不能改变创建时间
        assert (
            updated_ticket.created_at
            == original_created_at
        )

        # onupdate 应该把更新时间改成当前时间
        assert (
            updated_ticket.updated_at
            > old_updated_at
        )

    # 释放测试数据库连接
    test_engine.dispose()

# 验证 Repository 会稳定排序并执行 limit/offset 分页
def test_list_tickets_applies_stable_pagination() -> None:
    # 创建本测试独享的内存 SQLite 数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 根据 ORM 模型创建 tickets 表
    create_tables(test_engine)

    # 创建绑定测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine,
    )

    # 创建并自动关闭测试 Session
    with test_session_factory() as session:
        # 创建时间最早的工单
        oldest_ticket = make_ticket_record(
            "T-001",
        )

        # 设置创建时间
        oldest_ticket.created_at = datetime(
            2026,
            1,
            1,
        )

        # 设置初始更新时间
        oldest_ticket.updated_at = (
            oldest_ticket.created_at
        )

        # 创建第一张相同时间的工单
        same_time_second_ticket = make_ticket_record(
            "T-003",
        )

        # T-003 和 T-002 使用相同创建时间
        same_time_second_ticket.created_at = datetime(
            2026,
            1,
            3,
        )

        # 设置初始更新时间
        same_time_second_ticket.updated_at = (
            same_time_second_ticket.created_at
        )

        # 创建时间最新的工单
        newest_ticket = make_ticket_record(
            "T-004",
        )

        # 最新工单使用 1 月 4 日
        newest_ticket.created_at = datetime(
            2026,
            1,
            4,
        )

        # 设置初始更新时间
        newest_ticket.updated_at = (
            newest_ticket.created_at
        )

        # 创建第二张相同时间的工单
        same_time_first_ticket = make_ticket_record(
            "T-002",
        )

        # 与 T-003 使用相同创建时间
        same_time_first_ticket.created_at = datetime(
            2026,
            1,
            3,
        )

        # 设置初始更新时间
        same_time_first_ticket.updated_at = (
            same_time_first_ticket.created_at
        )

        # 故意不按照最终查询顺序插入
        # 用来证明结果来自 ORDER BY，而不是插入顺序
        for ticket in [
            oldest_ticket,
            same_time_second_ticket,
            newest_ticket,
            same_time_first_ticket,
        ]:
            # 保存当前测试工单
            create_ticket(
                session,
                ticket,
            )

        # 按照稳定顺序查询：
        # 完整顺序应为 T-004、T-002、T-003、T-001
        # offset=1 跳过 T-004
        # limit=2 只取 T-002 和 T-003
        page = list_tickets(
            session,
            limit=2,
            offset=1,
        )

        # 提取分页结果中的 ID，并验证顺序
        assert [
            ticket.id
            for ticket in page
        ] == [
            "T-002",
            "T-003",
        ]

    # 释放测试数据库连接
    test_engine.dispose()
