# 导入 pytest，用于 monkeypatch 和异常测试
import pytest

# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入需要测试的 Agent 工具模块
from ticketpilot.agent.tools import tickets as agent_tools

# 导入工具参数模型
from ticketpilot.agent.schemas import GetTicketToolArguments

# 导入数据库工单 ORM 模型
from ticketpilot.tickets.models import TicketRecord


# 验证查询到工单时，工具会返回安全的结构化数据
def test_get_ticket_details_returns_safe_ticket_data(
    # pytest 自动提供 monkeypatch
    monkeypatch,
) -> None:
    # 创建一张模拟的数据库工单
    ticket_record = TicketRecord(
        id="ticket-001",
        customer_name="测试客户",
        contact_email="customer@example.com",
        channel="email",
        subject="无法登录系统",
        description="输入密码后仍然无法登录",
        priority="high",
        status="open",
        assigned_to=None,
    )

    # 保存 Repository 实际收到的工单 ID
    received_ticket_ids: list[str] = []

    # 定义假的 Repository 查询函数
    def fake_get_ticket_by_id(
        # 接收工具传入的 Session
        session: Session,

        # 接收工具传入的工单 ID
        ticket_id: str,
    ) -> TicketRecord:
        # 记录工具传入的工单 ID
        received_ticket_ids.append(
            ticket_id,
        )

        # 模拟数据库成功查到工单
        return ticket_record

    # 临时替换真正的 Repository 查询函数
    monkeypatch.setattr(
        agent_tools.repository,
        "get_ticket_by_id",
        fake_get_ticket_by_id,
    )

    # 创建一个测试使用的 Session
    # with 结束后会自动关闭 Session
    with Session() as session:
        # 调用需要测试的查询工单工具
        result = agent_tools.get_ticket_details(
            session,
            GetTicketToolArguments(
                ticket_id="ticket-001",
            ),
        )

    # 工具应该把正确的 ID 交给 Repository
    assert received_ticket_ids == [
        "ticket-001",
    ]

    # 工具应该告诉 AI 已经找到工单
    assert result.found is True

    # 找到工单后 ticket 不应该是 None
    assert result.ticket is not None

    # 返回的工单 ID 应该正确
    assert result.ticket.id == "ticket-001"

    # 返回的工单标题应该正确
    assert result.ticket.subject == "无法登录系统"

    # 将 Pydantic 模型转换成字典
    safe_ticket_data = result.ticket.model_dump()

    # 不应该把客户姓名交给 AI
    assert "customer_name" not in safe_ticket_data

    # 不应该把客户邮箱交给 AI
    assert "contact_email" not in safe_ticket_data


# 验证查询不到工单时，工具会返回 found=False
def test_get_ticket_details_returns_not_found(
    monkeypatch,
) -> None:
    # 定义一个永远返回 None 的假查询函数
    def fake_get_ticket_by_id(
        session: Session,
        ticket_id: str,
    ) -> None:
        # None 表示数据库中没有这张工单
        return None

    # 临时替换真正的 Repository 查询函数
    monkeypatch.setattr(
        agent_tools.repository,
        "get_ticket_by_id",
        fake_get_ticket_by_id,
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 调用查询工具
        result = agent_tools.get_ticket_details(
            session,
            GetTicketToolArguments(
                ticket_id="missing-ticket",
            ),
        )

    # 工具应该明确表示没有找到
    assert result.found is False

    # 没找到时不应该返回工单数据
    assert result.ticket is None

    # 返回的信息应该稳定
    assert result.message == "工单不存在"


# 验证数据库故障不会被错误地转换成“工单不存在”
def test_get_ticket_details_propagates_repository_error(
    monkeypatch,
) -> None:
    # 定义一个模拟数据库故障的 Repository 函数
    def fake_get_ticket_by_id(
        session: Session,
        ticket_id: str,
    ) -> None:
        # 模拟 Repository 抛出的数据库错误
        raise RuntimeError(
            "查询工单失败",
        )

    # 临时替换真正的 Repository 查询函数
    monkeypatch.setattr(
        agent_tools.repository,
        "get_ticket_by_id",
        fake_get_ticket_by_id,
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 要求工具必须继续抛出 RuntimeError
        with pytest.raises(
            RuntimeError,
            match="查询工单失败",
        ):
            # 调用查询工具
            agent_tools.get_ticket_details(
                session,
                GetTicketToolArguments(
                    ticket_id="ticket-001",
                ),
            )
