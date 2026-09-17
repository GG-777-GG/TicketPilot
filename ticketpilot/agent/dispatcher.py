# 导入 SQLAlchemy Session 类型
# Dispatcher 会把当前请求的 Session 继续传给工具
from sqlalchemy.orm import Session

# 导入真正执行工作的 Agent 工具模块
from ticketpilot.agent.tools import tickets as agent_tools

# 导入工具请求、参数和结果模型
from ticketpilot.agent.schemas import (
    GetTicketToolArguments,
    GetTicketToolResult,
    ToolRequest,
)


# 表示 AI 请求了一个系统不支持的工具
class UnknownToolError(ValueError):
    # 当前不需要额外属性或方法
    pass


# 根据 AI 提供的工具名称分发并执行工具
def dispatch_tool(
    # 接收本次数据库操作使用的 Session
    session: Session,

    # 接收经过基础验证的完整工具请求
    request: ToolRequest,
) -> GetTicketToolResult:
    # 判断 AI 是否请求查询工单工具
    if request.name == "get_ticket_details":
        # 使用该工具专属的模型验证原始参数
        arguments = GetTicketToolArguments.model_validate(
            request.arguments,
        )

        # 参数合法后，调用真正的查询工单工具
        return agent_tools.get_ticket_details(
            session,
            arguments,
        )

    # 没有匹配到允许的工具，直接拒绝
    raise UnknownToolError(
        f"不支持的工具：{request.name}",
    )
