"""TicketPilot FastAPI 应用的装配入口。

本文件只负责创建应用和注册各业务模块的路由，不放具体业务逻辑。
"""

from fastapi import FastAPI
from contextlib import asynccontextmanager
from opentelemetry.sdk.trace.export import SpanExporter

from ticketpilot.agent.router import (
    router as agent_router,
)
from ticketpilot.approvals.router import (
    router as approvals_router,
)
from ticketpilot.audit.router import router as audit_router
from ticketpilot.identity.router import (
    router as identity_router,
)
from ticketpilot.tickets.router import (
    router as tickets_router,
)
from ticketpilot.knowledge.api.router import (
    router as knowledge_router,
)
from ticketpilot.observability.middleware import RequestContextMiddleware, unhandled_error_response
from ticketpilot.observability.tracing import build_tracer_provider


def health_check() -> dict[str, str]:
    """返回服务的基础健康状态。"""

    return {
        "status": "ok",
        "service": "TicketPilot",
    }


def create_app(*, span_exporter: SpanExporter | None = None) -> FastAPI:
    """创建并装配一个完整的 FastAPI 应用。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """每个应用生命周期创建一次Provider，退出时刷新并关闭。"""
        provider = build_tracer_provider(exporter=span_exporter)
        application.state.tracer_provider = provider
        try:
            yield
        finally:
            try:
                provider.force_flush(timeout_millis=5000)
            finally:
                try:
                    provider.shutdown()
                finally:
                    application.state.tracer_provider = None

    application = FastAPI(
        title="TicketPilot API",
        description="企业智能工单与知识库 Agent API",
        version="0.2.0",
        lifespan=lifespan,
        # 让 Swagger 在刷新页面后保留已经填写的授权。
        # 只保存授权信息，不延长 JWT 的有效期。
        swagger_ui_parameters={
            "persistAuthorization": True,
        },
    )

    # 请求编号贯穿认证、业务、独立失败审计和响应；未捕获异常单独补500头。
    application.add_middleware(RequestContextMiddleware)
    application.add_exception_handler(Exception, unhandled_error_response)

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

    # 注册审批申请接口，认证和事务分别由依赖与服务层负责。
    application.include_router(approvals_router)

    # 审计只有受角色限制的查询入口，不注册HTTP写入接口。
    application.include_router(audit_router)

    # 注册 Agent 对话接口
    application.include_router(agent_router)


    # 注册知识库文档管理接口。
    application.include_router(
        knowledge_router
    )

    return application

# ASGI 服务器和测试代码统一导入这个全局应用对象。
app = create_app()
