# 导入 json，用来把 JSON 字符串转换回 Python 字典
import json

# 导入 pytest，用于 monkeypatch 和异常测试
import pytest

# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入需要测试的 Agent Loop 模块
from ticketpilot import agent_loop

# 导入测试使用的 Fake Model
from ticketpilot.agent_model import FakeAgentModel

# 导入测试需要的数据模型
from ticketpilot.agent_schemas import (
    FinalAnswerDecision,
    GetTicketToolResult,
    TicketToolData,
    ToolCallDecision,
    ToolRequest,
)


# 验证模型直接回答时不会执行任何工具
def test_run_agent_returns_direct_answer_without_tool(
    monkeypatch,
) -> None:
    # 保存 Dispatcher 收到的工具请求
    tool_requests: list[ToolRequest] = []

    # 定义假的 Dispatcher
    def fake_dispatch_tool(
        session: Session,
        request: ToolRequest,
    ) -> GetTicketToolResult:
        # 如果被调用，就记录工具请求
        tool_requests.append(
            request,
        )

        # 返回测试结果
        return GetTicketToolResult(
            found=False,
            message="不应该执行工具",
            ticket=None,
        )

    # 临时替换真正的 Dispatcher
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    # 模型第一次就直接回答
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="你好，我是 TicketPilot。",
            ),
        ],
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 运行 Agent Loop
        answer = agent_loop.run_agent(
            session,
            model,
            "你好",
        )

    # 应该返回模型给出的最终回答
    assert answer == "你好，我是 TicketPilot。"

    # 最终回答不需要调用工具
    assert tool_requests == []

    # 模型只应该被调用一次
    assert len(model.received_messages) == 1

    # 模型第一次只应该看到用户消息
    assert [
        message.role
        for message in model.received_messages[0]
    ] == [
        "user",
    ]


# 验证 Agent 会先执行工具，再根据结果回答用户
def test_run_agent_calls_tool_then_returns_answer(
    monkeypatch,
) -> None:
    # 创建模型第一次返回的工具调用决定
    tool_decision = ToolCallDecision(
        kind="tool_call",
        tool_request=ToolRequest(
            name="get_ticket_details",
            arguments={
                "ticket_id": "ticket-001",
            },
        ),
    )

    # 创建模型第二次返回的最终回答
    final_decision = FinalAnswerDecision(
        kind="final_answer",
        answer="ticket-001 当前正在处理中。",
    )

    # 创建依次返回两个决定的 Fake Model
    model = FakeAgentModel(
        decisions=[
            tool_decision,
            final_decision,
        ],
    )

    # 创建假的工具执行结果
    tool_result = GetTicketToolResult(
        found=True,
        message="已找到工单",
        ticket=TicketToolData(
            id="ticket-001",
            subject="无法登录系统",
            description="输入密码后无法登录",
            priority="high",
            status="in_progress",
            assigned_to="测试客服",
        ),
    )

    # 保存 Dispatcher 收到的 Session
    received_sessions: list[Session] = []

    # 保存 Dispatcher 收到的工具请求
    received_requests: list[ToolRequest] = []

    # 定义假的 Dispatcher
    def fake_dispatch_tool(
        session: Session,
        request: ToolRequest,
    ) -> GetTicketToolResult:
        # 记录 Agent Loop 传入的 Session
        received_sessions.append(
            session,
        )

        # 记录 Agent Loop 传入的工具请求
        received_requests.append(
            request,
        )

        # 返回提前准备好的工具结果
        return tool_result

    # 临时替换真正的 Dispatcher
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 运行完整 Agent Loop
        answer = agent_loop.run_agent(
            session,
            model,
            "查询 ticket-001",
        )

    # 应该返回模型第二次给出的最终回答
    assert answer == "ticket-001 当前正在处理中。"

    # Dispatcher 应该只执行一次
    assert len(received_requests) == 1

    # Agent Loop 应该传递原来的 Session
    assert received_sessions[0] is session

    # 工具名称应该正确
    assert (
        received_requests[0].name
        == "get_ticket_details"
    )

    # 模型应该被调用两次
    assert len(model.received_messages) == 2

    # 第二次调用模型时应该有三条消息
    assert [
        message.role
        for message in model.received_messages[1]
    ] == [
        "user",
        "assistant",
        "tool",
    ]

    # 取得第二次模型调用时看到的工具结果消息
    tool_message = model.received_messages[1][2]

    # 将工具结果 JSON 转换成普通字典
    tool_data = json.loads(
        tool_message.content,
    )

    # 模型应该能看到工单状态
    assert (
        tool_data["ticket"]["status"]
        == "in_progress"
    )

    # 工具结果中不能出现客户邮箱
    assert (
        "contact_email"
        not in tool_data["ticket"]
    )


# 验证 Agent 不会无限执行工具
def test_run_agent_stops_at_tool_call_limit(
    monkeypatch,
) -> None:
    # 创建一个重复使用的工具调用决定
    tool_decision = ToolCallDecision(
        kind="tool_call",
        tool_request=ToolRequest(
            name="get_ticket_details",
            arguments={
                "ticket_id": "ticket-001",
            },
        ),
    )

    # 模型连续三次都请求调用工具
    model = FakeAgentModel(
        decisions=[
            tool_decision,
            tool_decision,
            tool_decision,
        ],
    )

    # 保存真正被 Dispatcher 执行的工具请求
    executed_requests: list[ToolRequest] = []

    # 定义假的 Dispatcher
    def fake_dispatch_tool(
        session: Session,
        request: ToolRequest,
    ) -> GetTicketToolResult:
        # 记录真正执行的工具请求
        executed_requests.append(
            request,
        )

        # 返回测试工具结果
        return GetTicketToolResult(
            found=False,
            message="工单不存在",
            ticket=None,
        )

    # 临时替换真正的 Dispatcher
    monkeypatch.setattr(
        agent_loop.agent_dispatcher,
        "dispatch_tool",
        fake_dispatch_tool,
    )

    # 创建并自动关闭测试 Session
    with Session() as session:
        # 最多只允许真正执行两次工具
        with pytest.raises(
            agent_loop.AgentLoopLimitError,
            match="工具调用次数超过限制",
        ):
            # 第三次工具请求应该在执行前被拒绝
            agent_loop.run_agent(
                session,
                model,
                "反复查询工单",
                max_tool_calls=2,
            )

    # 模型一共做出了三次决定
    assert len(model.received_messages) == 3

    # 但 Dispatcher 最多只执行了两次
    assert len(executed_requests) == 2