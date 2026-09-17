"""
Agent 对话 HTTP 接口。

业务目标：
接收已登录用户的问题，调用 TicketPilot Agent，
必要时让 Agent 查询工单，最后返回自然语言回答。

正常业务流程：
1. 客户端请求 POST /v1/agent/chat。
2. AgentChatRequest 验证用户问题。
3. CurrentUser 验证 JWT 和当前用户状态。
4. DatabaseSession 创建本次请求使用的数据库 Session。
5. AgentModelDependency 提供缓存的 OllamaModel。
6. run_agent() 把问题发送给模型。
7. 模型可以直接回答，也可以请求调用工单工具。
8. 工具通过 Repository 和 Session 查询数据库。
9. 模型根据工具结果生成最终回答。
10. 接口把回答包装成 AgentChatResponse。

异常流程：
- 请求数据不合法：FastAPI 自动返回 422。
- JWT 无效或缺失：CurrentUser 返回 401。
- 用户已停用：CurrentUser 返回 403。
- Ollama 无法连接或超时：返回 503。
- Agent 超过工具调用限制：返回 500。
- 工具或数据库发生异常：返回 500。

完整调用链：
客户端
→ Agent Router
→ Agent Loop
→ OllamaModel
→ Dispatcher
→ Ticket Tool
→ Ticket Repository
→ Database
"""


# 导入 FastAPI 路由、异常和状态码工具
from fastapi import (
    APIRouter,
    HTTPException,
    status,
)

# 导入 FastAPI 自动提供的 Agent 模型依赖
from ticketpilot.agent.dependencies import (
    AgentModelDependency,
)

# 导入 Agent Loop 和循环次数超限异常
from ticketpilot.agent.loop import (
    AgentLoopLimitError,
    run_agent,
)

# 导入 Ollama 调用异常
from ticketpilot.agent.providers.ollama import (
    OllamaModelError,
)

# 导入 Agent 接口的请求和响应 Schema
from ticketpilot.agent.schemas import (
    AgentChatRequest,
    AgentChatResponse,
)

# 导入数据库 Session 依赖
from ticketpilot.core.dependencies import (
    DatabaseSession,
)

# 导入当前登录用户依赖
from ticketpilot.identity.dependencies import (
    CurrentUser,
)


# 创建 Agent 路由分组
router = APIRouter(
    # 当前文件中的接口统一使用 /v1/agent 前缀
    prefix="/v1/agent",

    # 在 Swagger 文档中归入 agent 分组
    tags=["agent"],
)


# 与 TicketPilot Agent 进行一次对话
@router.post(
    # 完整地址是 POST /v1/agent/chat
    "/chat",

    # 限制成功响应必须符合 AgentChatResponse
    response_model=AgentChatResponse,
)
def chat_with_agent(
    # 接收并验证客户端提交的问题
    request: AgentChatRequest,

    # 获取本次请求独享的数据库 Session
    session: DatabaseSession,

    # 必须先通过 JWT 身份认证
    current_user: CurrentUser,

    # FastAPI 自动取得缓存的 OllamaModel
    model: AgentModelDependency,
) -> AgentChatResponse:
    # 捕获 Agent 执行过程中可能出现的已知错误
    try:
        # 运行完整 Agent 循环
        answer = run_agent(
            # 工具查询工单时使用当前请求的 Session
            session,

            # 使用依赖注入提供的模型
            model,

            # 把客户端问题交给 Agent
            request.message,
        )

    # Ollama 没启动、超时或返回无效内容
    except OllamaModelError as error:
        raise HTTPException(
            # 503 表示依赖的模型服务暂时不可用
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,

            # 不向客户端泄露底层连接细节
            detail="AI 模型服务暂时不可用",
        ) from error

    # Agent 连续调用工具超过安全次数
    except AgentLoopLimitError as error:
        raise HTTPException(
            # 这是服务器执行流程未能正常完成
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,

            # 返回稳定的业务错误信息
            detail="Agent 未能在安全限制内完成请求",
        ) from error

    # 捕获工具或 Repository 抛出的其他运行时异常
    except RuntimeError as error:
        raise HTTPException(
            # 返回统一的服务器内部错误
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,

            # 不泄露数据库或者内部实现细节
            detail="Agent 服务暂时不可用",
        ) from error

    # 把 Agent 返回的字符串包装成 API 响应
    return AgentChatResponse(
        answer=answer,
    )