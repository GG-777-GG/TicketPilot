"""
文件职责：通过MCP暴露只读工单查询工具。
作用与配合：复用身份验证和已有工单查询工具。
使用位置：后续由可信后端通过stdio子进程启动。
边界：不审核、不关闭工单，不接受模型自报用户或角色。
"""

import os
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.exc import SQLAlchemyError

from ticketpilot.agent.schemas import (
    GetTicketToolArguments,
    GetTicketToolResult,
)
from ticketpilot.agent.tools import tickets as ticket_tools
from ticketpilot.core.database import SessionLocal
from ticketpilot.identity import repository as user_repository
from ticketpilot.identity.security import (
    InvalidAccessTokenError,
    decode_access_token,
)


server = MCPServer("TicketPilot Readonly Tickets")


@server.tool()
def get_ticket_details(ticket_id: UUID) -> GetTicketToolResult:
    """根据真实工单UUID查询详情；只读取，不修改工单。"""

    # 令牌由可信后端启动子进程时提供，不属于模型工具参数。
    token = os.environ.get("TICKETPILOT_MCP_TOKEN", "")

    if not token:
        raise ToolError("缺少MCP访问身份")

    try:
        user_id = decode_access_token(token)
    except InvalidAccessTokenError:
        raise ToolError("MCP访问身份无效或已过期") from None

    try:
        # 每次调用使用新Session，重新获取当前账号状态。
        with SessionLocal() as session:
            user = user_repository.get_user_by_id(
                session,
                user_id,
            )

            if (
                user is None
                or not user.is_active
                or user.role not in ("agent", "supervisor", "admin")
            ):
                raise ToolError("当前账号不能查询工单")

            # 复用原来的查询和公开字段转换，不重复写业务SQL。
            return ticket_tools.get_ticket_details(
                session,
                GetTicketToolArguments(
                    ticket_id=str(ticket_id),
                ),
            )

    except ToolError:
        raise

    except (RuntimeError, SQLAlchemyError):
        # 不向工具调用方泄漏连接地址、SQL等内部信息。
        raise ToolError("工单查询服务暂时不可用") from None


if __name__ == "__main__":
    server.run(transport="stdio")