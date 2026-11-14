"""
文件职责：通过MCP Client查询只读工单Server。
作用与配合：启动固定的本地Server，验证协议结果并转换为业务模型。
使用位置：后续由Agent工具适配层调用。
边界：不从模型参数读取身份、命令或数据库配置，不执行工单写入。
"""

import asyncio
import sys
from uuid import UUID

from mcp import Client, StdioServerParameters

from ticketpilot.agent.schemas import GetTicketToolResult
from ticketpilot.core.config import BASE_DIR, settings
from ticketpilot.observability.spans import operation_span


class McpTicketError(RuntimeError):
    """MCP查询无法提供有效结果。"""


def query_ticket_via_mcp_sync(
    ticket_id: UUID,
    *,
    access_token: str,
) -> GetTicketToolResult:
    """供同步Agent工作线程调用；已有事件循环的调用方应直接await异步函数。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(query_ticket_via_mcp(ticket_id, access_token=access_token))
    raise McpTicketError("同步MCP适配器不能在事件循环线程中调用")


async def query_ticket_via_mcp(
    ticket_id: UUID,
    *,
    access_token: str,
) -> GetTicketToolResult:
    """观察完整连接/发现/调用/校验，异常与取消仍按原路径传播。"""
    with operation_span("mcp.client", tool_name="get_ticket_details"):
        return await _query_ticket_via_mcp(ticket_id, access_token=access_token)


async def _query_ticket_via_mcp(
    ticket_id: UUID,
    *,
    access_token: str,
) -> GetTicketToolResult:
    """连接只读Server，查询并验证指定工单的返回结果。"""

    if not access_token:
        raise McpTicketError("缺少MCP访问身份")

    # 使用当前项目的Python解释器和固定模块。
    # 模型不能指定命令、脚本或启动目录。
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "ticketpilot.integrations.mcp_server",
        ],
        cwd=BASE_DIR,
        env={
            # 全部来自可信后端配置，不接受模型填写。
            "DATABASE_URL": settings.database_url,
            "JWT_SECRET_KEY": settings.jwt_secret_key.get_secret_value(),
            "TICKETPILOT_MCP_TOKEN": access_token,
            "PYTHONIOENCODING": "utf-8",
        },
    )

    try:
        # 对连接、发现和查询设置整体等待期限。
        async with asyncio.timeout(15):
            async with Client(
                parameters,
                read_timeout_seconds=5,
            ) as client:
                tools = await client.list_tools()

                if not any(
                    tool.name == "get_ticket_details"
                    for tool in tools.tools
                ):
                    raise McpTicketError("MCP未提供工单查询工具")

                result = await client.call_tool(
                    "get_ticket_details",
                    {"ticket_id": str(ticket_id)},
                )

                # 收到协议响应后，还需要检查工具执行是否成功。
                if result.is_error:
                    raise McpTicketError(
                        "MCP查询失败，请检查身份、权限或服务状态"
                    )

                # 本项目约定工具返回结构化业务数据。
                data = GetTicketToolResult.model_validate(
                    result.structured_content,
                )

                # 防止结果内部矛盾或返回了另一张工单。
                if data.found != (data.ticket is not None):
                    raise McpTicketError("MCP返回的工单结果不一致")

                if (
                    data.ticket is not None
                    and data.ticket.id != str(ticket_id)
                ):
                    raise McpTicketError("MCP返回的工单编号不匹配")

                return data

    except McpTicketError:
        raise

    except TimeoutError:
        raise McpTicketError("MCP工单查询超时") from None

    except Exception:
        # 外部协议边界统一错误，避免公开启动环境或内部异常。
        raise McpTicketError("MCP工单查询服务暂时不可用") from None
