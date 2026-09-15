# 导入 SQLAlchemy Session 类型
# 工具需要通过 Session 访问本次请求的数据库连接
from sqlalchemy.orm import Session

# 导入现有的工单 Repository 模块
# 工具不自己写 SQL，而是复用 Repository
from ticketpilot import repository

# 导入工具的参数模型和结果模型
from ticketpilot.agent_schemas import (
    GetTicketToolArguments,
    GetTicketToolResult,
    TicketToolData,
)


# 根据工单 ID 查询工单详情
def get_ticket_details(
    # 接收本次数据库操作使用的 Session
    session: Session,

    # 接收已经由 Pydantic 验证过的工具参数
    arguments: GetTicketToolArguments,
) -> GetTicketToolResult:
    # 调用现有 Repository，根据主键查询数据库
    ticket = repository.get_ticket_by_id(
        session,
        arguments.ticket_id,
    )

    # Repository 返回 None，表示数据库中没有这张工单
    if ticket is None:
        # 返回结构固定的“没有找到”结果
        return GetTicketToolResult(
            found=False,
            message="工单不存在",
            ticket=None,
        )

    # 只挑选允许交给 AI 的工单字段
    safe_ticket = TicketToolData(
        id=ticket.id,
        subject=ticket.subject,
        description=ticket.description,
        priority=ticket.priority,
        status=ticket.status,
        assigned_to=ticket.assigned_to,
    )

    # 返回结构固定的“查询成功”结果
    return GetTicketToolResult(
        found=True,
        message="已找到工单",
        ticket=safe_ticket,
    )