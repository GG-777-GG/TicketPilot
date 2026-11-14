# 导入 SQLAlchemy Session 类型
# Dispatcher 会把当前请求的 Session 继续传给工具
from sqlalchemy.orm import Session

# dataclass 用来集中保存 Agent 工具运行时依赖。
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
from pydantic import TypeAdapter
from ticketpilot.identity.models import UserRecord
from ticketpilot.identity.security import create_access_token
from ticketpilot.integrations.mcp_client import query_ticket_via_mcp_sync
from ticketpilot.approvals.service import ApprovalAccessError
from ticketpilot.agent.tools import approvals as approval_tools

# 导入真正执行工作的 Agent 工具模块。
from ticketpilot.agent.tools import (
    knowledge as knowledge_tools,
)
from ticketpilot.agent.tools import (
    tickets as ticket_tools,
)

# 知识检索工具需要使用向量模型。
from ticketpilot.knowledge.embeddings.base import (
    EmbeddingProvider,
)

from ticketpilot.agent.schemas import (
    GetTicketToolArguments,
    GetTicketToolResult,
    SearchKnowledgeToolArguments,
    SearchKnowledgeToolResult,
    ToolRequest,
    RequestTicketClosureArguments,
    RequestTicketClosureResult,
)

# 集中保存一次 Agent 运行期间所有工具共享的依赖。
@dataclass(
    frozen=True,
    slots=True,
)
class AgentToolContext:
    # 当前请求独享的数据库 Session。
    session: Session

    # 当前 Agent 使用的 Embedding Provider。
    embedding_provider: EmbeddingProvider

    # 本次运行允许知识工具检索的文档类型。
    allowed_visibilities: tuple[str, ...] = ("internal",)

    # 只由认证入口与原始用户消息提供，模型不能填写身份或授权对象。
    current_user: UserRecord | None = None
    checkpoint_path: Path | None = None
    closure_request: RequestTicketClosureArguments | None = None

    # 正式聊天入口明确启用MCP；旧独立示例和纯单元测试保留本地模式。
    use_mcp_tickets: bool = False


# 表示 AI 请求了一个系统不支持的工具
class UnknownToolError(ValueError):
    # 当前不需要额外属性或方法
    pass

# 根据模型提供的工具名称分发并执行工具。
def dispatch_tool(
    # 保存 Session 和 Embedding Provider。
    context: AgentToolContext,

    # 接收模型生成的完整工具请求。
    request: ToolRequest,
) -> (
    GetTicketToolResult
    | SearchKnowledgeToolResult
    | RequestTicketClosureResult
):
    if request.name == "request_ticket_closure":
        arguments = RequestTicketClosureArguments.model_validate(request.arguments)
        if (
            context.current_user is None
            or context.checkpoint_path is None
            or context.closure_request is None
            or arguments != context.closure_request
        ):
            raise ApprovalAccessError("请明确提供工单编号和理由，并要求提交关闭申请")
        return approval_tools.request_ticket_closure(
            context.session, arguments,
            requester=context.current_user, checkpoint_path=context.checkpoint_path,
        )

    # 模型请求查询具体工单。
    if request.name == "get_ticket_details":
        # 使用工单工具专属 Schema 验证参数。
        arguments = (
            GetTicketToolArguments.model_validate(
                request.arguments
            )
        )

        if context.use_mcp_tickets:
            user = context.current_user
            if user is None or not user.is_active or user.role not in ("agent", "supervisor", "admin"):
                raise ApprovalAccessError("当前身份不能查询MCP工单工具")
            ticket_id = TypeAdapter(UUID).validate_python(arguments.ticket_id)
            # 身份由可信后端签发；模型仅提供工单编号。
            return query_ticket_via_mcp_sync(
                ticket_id, access_token=create_access_token(user.id),
            )

        # 显式本地模式用于原独立示例；MCP失败不会进入此分支。
        return ticket_tools.get_ticket_details(
            context.session,
            arguments,
        )

    # 模型请求查询企业知识库。
    if request.name == "search_knowledge":
        # 使用知识工具专属 Schema 验证参数。
        arguments = (
            SearchKnowledgeToolArguments
            .model_validate(
                request.arguments
            )
        )

        # 知识工具需要 Session 和 Embedding Provider。
        return knowledge_tools.search_knowledge(
            context.session,
            context.embedding_provider,
            arguments,
            allowed_visibilities=context.allowed_visibilities,
        )

    # 工具名称不在系统允许列表中。
    raise UnknownToolError(
        f"不支持的工具：{request.name}"
    )
