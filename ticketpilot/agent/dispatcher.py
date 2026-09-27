# 导入 SQLAlchemy Session 类型
# Dispatcher 会把当前请求的 Session 继续传给工具
from sqlalchemy.orm import Session

# dataclass 用来集中保存 Agent 工具运行时依赖。
from dataclasses import dataclass

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
):
    # 模型请求查询具体工单。
    if request.name == "get_ticket_details":
        # 使用工单工具专属 Schema 验证参数。
        arguments = (
            GetTicketToolArguments.model_validate(
                request.arguments
            )
        )

        # 工单工具只需要数据库 Session。
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
        )

    # 工具名称不在系统允许列表中。
    raise UnknownToolError(
        f"不支持的工具：{request.name}"
    )