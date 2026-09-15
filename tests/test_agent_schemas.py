# 导入 pytest，用来验证代码是否抛出预期异常
import pytest

# 导入 Pydantic 的数据验证异常
from pydantic import ValidationError

# 导入需要测试的工具参数模型
from ticketpilot.agent_schemas import GetTicketToolArguments


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