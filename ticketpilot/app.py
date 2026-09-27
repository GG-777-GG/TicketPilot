"""TicketPilot FastAPI 应用的装配入口。

本文件只负责创建应用和注册各业务模块的路由，不放具体业务逻辑。
"""

from fastapi import FastAPI

from ticketpilot.agent.router import (
    router as agent_router,
)
from ticketpilot.identity.router import (
    router as identity_router,
)
from ticketpilot.tickets.router import (
    router as tickets_router,
)
from ticketpilot.knowledge.router import (
    router as knowledge_router,
)


def health_check() -> dict[str, str]:
    """返回服务的基础健康状态。"""

    return {
        "status": "ok",
        "service": "TicketPilot",
    }


def create_app() -> FastAPI:
    """创建并装配一个完整的 FastAPI 应用。"""

    application = FastAPI(
        title="TicketPilot API",
        description="企业智能工单与知识库 Agent API",
        version="0.2.0",
    )

    application.add_api_route(
        "/health",
        health_check,
        methods=["GET"],
        tags=["health"],
    )
    # 注册用户认证接口
    application.include_router(identity_router)

    # 注册工单业务接口
    application.include_router(tickets_router)

    # 注册 Agent 对话接口
    application.include_router(agent_router)


    # 注册知识库文档管理接口。
    application.include_router(
        knowledge_router
    )

    return application

# ASGI 服务器和测试代码统一导入这个全局应用对象。
app = create_app()
