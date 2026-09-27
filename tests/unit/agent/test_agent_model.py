# 导入 pytest，用于验证预期异常
import pytest

# 导入需要测试的 Fake Model
from ticketpilot.agent.model import FakeAgentModel

# 导入测试需要使用的消息和决定模型
from ticketpilot.agent.schemas import (
    AgentMessage,
    FinalAnswerDecision,
    ToolCallDecision,
    ToolRequest,
)


# 验证 Fake Model 会按顺序返回预设决定
def test_fake_agent_model_returns_decisions_in_order() -> None:
    # 创建第一次应该返回的工具调用决定
    tool_decision = ToolCallDecision(
        kind="tool_call",
        tool_request=ToolRequest(
            name="get_ticket_details",
            arguments={
                "ticket_id": "ticket-001",
            },
        ),
    )

    # 创建第二次应该返回的最终回答决定
    final_decision = FinalAnswerDecision(
        kind="final_answer",
        answer="工单当前处于处理中。",
    )

    # 保存原始决定列表
    prepared_decisions = [
        tool_decision,
        final_decision,
    ]

    # 创建按照预设顺序工作的 Fake Model
    model = FakeAgentModel(
        decisions=prepared_decisions,
    )

    # 创建模型第一次收到的消息
    messages = [
        AgentMessage(
            role="user",
            content="查询 ticket-001",
        ),
    ]

    # 第一次调用模型
    first_decision = model.decide(
        messages,
    )

    # 把模拟工具结果添加到消息历史
    messages.append(
        AgentMessage(
            role="tool",
            content='{"status":"in_progress"}',
        ),
    )

    # 第二次调用模型
    second_decision = model.decide(
        messages,
    )

    # 第一次应该返回工具调用决定
    assert first_decision == tool_decision

    # 第二次应该返回最终回答决定
    assert second_decision == final_decision

    # Fake Model 不应该修改调用者原来的决定列表
    assert len(prepared_decisions) == 2

    # 模型应该一共被调用两次
    assert len(model.received_messages) == 2

    # 第一次记录的消息快照应该只有用户消息
    assert len(model.received_messages[0]) == 1

    # 第二次记录的消息应该包含用户和工具消息
    assert [
        message.role
        for message in model.received_messages[1]
    ] == [
        "user",
        "tool",
    ]


# 验证预设决定耗尽时 Fake Model 会明确报错
def test_fake_agent_model_raises_when_decisions_are_exhausted() -> None:
    # 创建只有一个决定的 Fake Model
    model = FakeAgentModel(
        decisions=[
            FinalAnswerDecision(
                kind="final_answer",
                answer="测试回答",
            ),
        ],
    )

    # 创建测试消息
    messages = [
        AgentMessage(
            role="user",
            content="测试问题",
        ),
    ]

      # 第一次调用会正常使用唯一的预设决定
    model.decide(
        messages,
    )

    # 第二次调用时已经没有剩余决定
    with pytest.raises(
        RuntimeError,
        match="没有剩余的预设决定",
    ):
        model.decide(
            messages,
        )
