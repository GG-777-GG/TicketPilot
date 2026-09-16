# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入工具分发器模块
from ticketpilot import agent_dispatcher

# 导入所有模型必须遵守的接口
from ticketpilot.agent_model import AgentModel

# 导入消息和模型决定类型
from ticketpilot.agent_schemas import (
    AgentMessage,
    FinalAnswerDecision,
    ToolCallDecision,
)


# 表示 Agent 调用工具的次数超过安全限制
class AgentLoopLimitError(RuntimeError):
    # 当前不需要添加额外属性或方法
    pass


# 运行一次完整的 Agent 对话循环
def run_agent(
    # 接收本次请求使用的数据库 Session
    session: Session,

    # 接收真实模型或者 Fake Model
    model: AgentModel,

    # 接收用户本次提出的问题
    user_message: str,

    # 星号后面的参数必须使用参数名传递
    *,

    # 单次 Agent 运行最多允许执行三个工具
    max_tool_calls: int = 3,
) -> str:
    # 工具调用次数至少必须允许一次
    if max_tool_calls < 1:
        # 拒绝无效的循环配置
        raise ValueError(
            "max_tool_calls 必须至少为 1",
        )

    # 使用用户输入创建初始消息历史
    messages: list[AgentMessage] = [
        AgentMessage(
            role="user",
            content=user_message,
        ),
    ]

    # 记录已经真正执行了多少次工具
    tool_call_count = 0

    # 持续运行，直到模型给出最终回答或者发生错误
    while True:
        # 把当前消息历史交给模型
        decision = model.decide(
            # 传入列表副本，防止模型修改原列表
            messages.copy(),
        )

        # 如果模型决定直接回答用户
        if isinstance(
            decision,
            FinalAnswerDecision,
        ):
            # 返回最终回答并结束整个循环
            return decision.answer

        # 防御模型实现返回不支持的数据类型
        if not isinstance(
            decision,
            ToolCallDecision,
        ):
            # 这表示模型没有遵守 AgentModel 接口
            raise TypeError(
                "模型返回了不支持的决定类型",
            )

        # 执行新工具前检查次数是否已经到达上限
        if tool_call_count >= max_tool_calls:
            # 在危险工具真正执行前终止循环
            raise AgentLoopLimitError(
                "Agent 工具调用次数超过限制",
            )

        # 通过 Dispatcher 验证并执行工具
        tool_result = agent_dispatcher.dispatch_tool(
            session,
            decision.tool_request,
        )

        # 把模型刚才的工具请求加入消息历史
        messages.append(
            AgentMessage(
                role="assistant",

                # 将 Pydantic 对象转换成 JSON 字符串
                content=decision.model_dump_json(),
            ),
        )

        # 把 Python 工具的执行结果加入消息历史
        messages.append(
            AgentMessage(
                role="tool",

                # 将工具结果转换成 JSON 字符串
                content=tool_result.model_dump_json(),
            ),
        )

        # 工具成功执行后增加计数
        tool_call_count += 1