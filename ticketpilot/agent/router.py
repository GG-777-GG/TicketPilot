"""
Agent 对话 HTTP 接口。

业务目标：
接收已登录用户的问题，调用 TicketPilot Agent，
必要时查询工单或创建受控关闭申请，返回自然语言回答或后端申请结果。

正常业务流程：
1. 客户端请求 POST /v1/agent/chat。
2. AgentChatRequest 验证用户问题。
3. CurrentUser 验证 JWT 和当前用户状态。
4. DatabaseSession 创建本次请求使用的数据库 Session。
5. AgentModelDependency 提供缓存的 OllamaModel。
6. run_agent() 把问题发送给模型。
7. 模型可以回答或调用白名单工具；申请参数须匹配原始用户明确指令。
8. 工具通过 Repository 和 Session 查询数据库。
9. 只读结果继续交给模型；关闭申请保存后直接返回后端生成的申请结果。
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
from pydantic import ValidationError
from ticketpilot.approvals import service as approval_service
from ticketpilot.core.config import settings
from ticketpilot.integrations.mcp_client import McpTicketError
from ticketpilot.observability.spans import operation_span

# 导入 FastAPI 自动提供的 Agent 模型依赖
from ticketpilot.agent.dependencies import (
    AgentModelDependency,
    # FastAPI 自动提供可选的证据校验器。
    EvidenceVerifierDependency,
)

# FastAPI 自动提供知识检索使用的 Embedding Provider。
from ticketpilot.knowledge.api.dependencies import (
    EmbeddingProviderDependency,
)

# 导入 Agent Loop 和循环次数超限异常
from ticketpilot.agent.loop import (
    InvalidAgentEvidenceError,
    AgentLoopLimitError,
    run_agent,
)

# 导入 Ollama 调用异常
from ticketpilot.agent.providers.ollama import (
    OllamaModelError,
)

# 导入证据校验失败异常，用来区分模型故障和“没有答案”。
from ticketpilot.agent.verification import (
    EvidenceVerificationError,
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

# 根据已认证用户的角色确定知识检索范围。
from ticketpilot.knowledge.domain.access import (
    allowed_visibilities_for_role,
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

    # FastAPI 自动取得缓存的 Embedding Provider。
    embedding_provider: EmbeddingProviderDependency,

    # 从配置开关取得校验器；默认是 None。
    evidence_verifier: EvidenceVerifierDependency,
) -> AgentChatResponse:

    # 权限只从当前登录用户取得，不接受模型或请求自行指定。
    allowed_visibilities = allowed_visibilities_for_role(
        current_user.role
    )

    # 捕获 Agent 执行过程中可能出现的已知错误
    try:
        # Span仅观察完整调用，不改变返回值、权限或异常转换。
        with operation_span("agent.run"):
            result = run_agent(
                # 工具访问数据库时使用的 Session。
                session,

                # 负责决定是否调用工具的对话模型。
                model,

                # 用户提交的问题。
                request.message,

                # search_knowledge 工具生成问题向量时使用。
                embedding_provider=embedding_provider,

                # knowledge 模式要求后端先检索，auto 模式保持原有行为。
                require_knowledge_search=(request.mode == "knowledge"),

                # 把校验器交给 Agent Loop；None 表示不启用。
                evidence_verifier=evidence_verifier,

                # 传给 Agent Loop，再由工具上下文继续传递。
                allowed_visibilities=allowed_visibilities,
                current_user=current_user,
                checkpoint_path=settings.approval_workflow_checkpoint_path,
                use_mcp_tickets=True,
            )

    except McpTicketError as error:
        raise HTTPException(status_code=503, detail="MCP工单查询服务暂时不可用") from error
    except approval_service.ApprovalAccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except approval_service.ApprovalTicketNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except approval_service.ApprovalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="模型提供的工具参数不符合要求") from error

    # 对话模型或证据校验服务失败时，统一返回 HTTP 503。
    except (OllamaModelError, EvidenceVerificationError) as error:
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

    # 模型引句无效时，不把错误或未经证实的答案交给用户。
    except InvalidAgentEvidenceError:
        return AgentChatResponse(
            answer="根据当前检索结果，无法确认答案。",
            sources=[],
        )

    # 捕获工具或 Repository 抛出的其他运行时异常
    except RuntimeError as error:
        raise HTTPException(
            # 返回统一的服务器内部错误
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,

            # 不泄露数据库或者内部实现细节
            detail="Agent 服务暂时不可用",
        ) from error

    # 把 Agent 的答案和真实来源包装成 API 响应。
    return AgentChatResponse(
        answer=result.answer,
        sources=list(
            result.sources
        ),
    )
