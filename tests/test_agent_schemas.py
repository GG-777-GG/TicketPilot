# 导入 pytest，用来验证代码是否抛出预期异常
import pytest

# 导入联合类型验证器和数据验证异常
from pydantic import (
    TypeAdapter,
    ValidationError,
)

# 导入需要测试的 Agent 数据模型
from ticketpilot.agent_schemas import (
    FinalAnswerDecision,
    GetTicketToolArguments,
    ModelDecision,
    ToolCallDecision,
)

# 为 ModelDecision 联合类型创建 Pydantic 验证器
MODEL_DECISION_ADAPTER = TypeAdapter(
    ModelDecision,
)

# 验证合法的工单 ID 可以通过检查
def test_get_ticket_tool_arguments_accepts_valid_id() -> None:
    # 模拟 AI 生成一个两端带空格的工单 ID
    arguments = GetTicketToolArguments(
        ticket_id="  ticket-001  ",
    )

    # ConfigDict 应该自动删除 ID 两端的空格
    assert arguments.ticket_id == "ticket-001"


# 验证全部为空格的工单 ID 会被拒绝
def test_get_ticket_tool_arguments_rejects_blank_id() -> None:
    # 表示下面的代码必须抛出 Pydantic 验证异常
    with pytest.raises(
        ValidationError,
    ):
        # 空格被删除后会变成空字符串
        GetTicketToolArguments(
            ticket_id="   ",
        )


# 验证 AI 生成的未知参数会被拒绝
def test_get_ticket_tool_arguments_rejects_extra_fields() -> None:
    # 模拟 AI 生成的原始工具参数
    invalid_arguments = {
        # 这是工具允许接收的参数
        "ticket_id": "ticket-001",

        # 这是工具没有声明的危险参数
        "delete_ticket": True,
    }

    # extra="forbid" 应该使下面的验证失败
    with pytest.raises(
        ValidationError,
    ):
        # model_validate 用来验证字典形式的原始数据
        GetTicketToolArguments.model_validate(
            invalid_arguments,
        )

# 验证模型可以选择直接回答用户
def test_model_decision_accepts_final_answer() -> None:
    # 模拟模型生成的原始结构化数据
    raw_decision = {
        "kind": "final_answer",
        "answer": "  工单当前正在处理中。  ",
    }

    # 按照 ModelDecision 联合类型进行验证
    decision = MODEL_DECISION_ADAPTER.validate_python(
        raw_decision,
    )

    # 验证器应该选择 FinalAnswerDecision
    assert isinstance(
        decision,
        FinalAnswerDecision,
    )

    # 回答两端的空格应该被自动删除
    assert decision.answer == "工单当前正在处理中。"


# 验证模型可以选择调用查询工单工具
def test_model_decision_accepts_tool_call() -> None:
    # 模拟模型生成的工具调用数据
    raw_decision = {
        "kind": "tool_call",
        "tool_request": {
            "name": "get_ticket_details",
            "arguments": {
                "ticket_id": "ticket-001",
            },
        },
    }

    # 验证原始模型数据
    decision = MODEL_DECISION_ADAPTER.validate_python(
        raw_decision,
    )

    # 验证器应该选择 ToolCallDecision
    assert isinstance(
        decision,
        ToolCallDecision,
    )

    # 验证工具名称正确
    assert (
        decision.tool_request.name
        == "get_ticket_details"
    )

    # 验证工具参数正确
    assert (
        decision.tool_request.arguments["ticket_id"]
        == "ticket-001"
    )


# 验证最终回答和工具请求不能混在一起
def test_model_decision_rejects_mixed_structure() -> None:
    # 模拟一个结构矛盾的模型决定
    invalid_decision = {
        # 声称这是最终回答
        "kind": "final_answer",

        # 提供了最终回答内容
        "answer": "准备查询工单",

        # 却又同时请求调用工具
        "tool_request": {
            "name": "get_ticket_details",
            "arguments": {
                "ticket_id": "ticket-001",
            },
        },
    }

    # 联合类型必须拒绝这个矛盾结构
    with pytest.raises(
        ValidationError,
    ):
        MODEL_DECISION_ADAPTER.validate_python(
            invalid_decision,
        )