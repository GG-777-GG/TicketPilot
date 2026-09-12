# 导入 Annotated，用于组合类型和 FastAPI 依赖信息
from typing import Annotated

# 导入 UUID 生成函数，用于创建工单编号
from uuid import uuid4

# 导入 FastAPI 使用的主要工具
from fastapi import (
    # 声明参数由依赖注入系统提供
    Depends,
    # 创建 FastAPI 应用
    FastAPI,
    # 返回 HTTP 错误
    HTTPException,
    # 提供带名称的 HTTP 状态码
    status,
)

# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入 Repository，负责数据库 CRUD
from ticketpilot import repository as ticket_repository

# 导入每个请求使用的 Session 依赖
from ticketpilot.database import get_session

# 导入数据库工单 ORM 模型
from ticketpilot.db_models import TicketRecord

# 导入 API 请求和响应模型
from ticketpilot.schemas import (
    TicketAssigneeUpdateRequest,
    TicketCreateRequest,
    TicketResponse,
    TicketStatusUpdateRequest,
)


# 定义由 FastAPI 自动提供的数据库 Session 参数类型
DatabaseSession = Annotated[
    # 路由函数实际得到的是 SQLAlchemy Session
    Session,
    # FastAPI 调用 get_session() 创建并提供 Session
    Depends(get_session),
]


# 创建 FastAPI 应用
app = FastAPI(
    # Swagger 文档中显示的项目名称
    title="TicketPilot API",
    # Swagger 文档中显示的项目说明
    description="企业智能工单与知识库 Agent API",
    # 当前 API 版本
    version="0.2.0",
)

# 将数据库 ORM 工单转换成 API 响应模型
def to_ticket_response(
    # 接收从数据库查询出来的 TicketRecord
    ticket: TicketRecord,
) -> TicketResponse:
    # 创建并返回 Pydantic 响应对象
    return TicketResponse(
        # 返回工单 ID
        id=ticket.id,

        # 返回客户姓名
        customer_name=ticket.customer_name,

        # 返回联系邮箱
        contact_email=ticket.contact_email,

        # 返回工单渠道
        channel=ticket.channel,

        # 返回工单标题
        subject=ticket.subject,

        # 返回工单描述
        description=ticket.description,

        # 返回优先级
        priority=ticket.priority,

        # 返回工单状态
        status=ticket.status,

        # 返回当前处理人
        assigned_to=ticket.assigned_to,

        # 返回数据库生成的创建时间
        created_at=ticket.created_at,

        # 返回数据库维护的最后更新时间
        updated_at=ticket.updated_at,
    )


# 返回服务当前的基础健康状态
@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "TicketPilot"
    }


# 查询全部工单
# 从数据库查询全部工单
@app.get(
    # 定义接口地址
    "/v1/tickets",
    # 声明响应是 TicketResponse 列表
    response_model=list[TicketResponse],
    # 在 Swagger 文档中归入 tickets 分组
    tags=["tickets"],
)
def list_tickets(
        # 由 FastAPI 为本次请求注入数据库 Session
        session: DatabaseSession,
        # 返回 TicketResponse 列表
) -> list[TicketResponse]:
    # 捕获 Repository 转换后的数据库异常
    try:
        # 调用 Repository，从数据库取得 ORM 工单列表
        ticket_records = ticket_repository.list_tickets(
            session
        )

    # 捕获 Repository 提供的稳定 RuntimeError
    except RuntimeError as error:
        # 转换成不泄漏数据库细节的 HTTP 500
        raise HTTPException(
            # 设置内部服务错误状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 设置客户端可以看到的统一错误消息
            detail="工单服务暂时不可用",
            # 保留原始异常链
        ) from error

    # 逐个把 TicketRecord 转换成 TicketResponse
    return [
        # 转换当前 ORM 工单
        to_ticket_response(ticket_record)
        # 遍历数据库查询结果
        for ticket_record in ticket_records
    ]


# 根据工单 ID 从数据库查询一张工单
@app.get(
    # ticket_id 是客户端提供的路径参数
    "/v1/tickets/{ticket_id}",
    # 声明成功响应的结构
    response_model=TicketResponse,
    # 在 Swagger 文档中归入 tickets 分组
    tags=["tickets"],
)
def get_ticket(
        # 接收 URL 中的工单 ID
        ticket_id: str,
        # 获取本次请求独享的数据库 Session
        session: DatabaseSession,
        # 找到工单时返回 TicketResponse
) -> TicketResponse:
    # 捕获 Repository 数据库错误
    try:
        # 根据主键查询 ORM 工单
        ticket_record = ticket_repository.get_ticket_by_id(
            # 传入当前请求的 Session
            session,
            # 传入客户端请求的工单 ID
            ticket_id,
        )

    # 捕获 Repository 转换后的 RuntimeError
    except RuntimeError as error:
        # 数据库故障转换成 HTTP 500
        raise HTTPException(
            # 返回内部服务错误
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 不向客户端泄漏数据库错误细节
            detail="工单服务暂时不可用",
            # 保留原异常作为异常链
        ) from error

    # Repository 返回 None 表示数据库中没有这张工单
    if ticket_record is None:
        # 向客户端返回资源不存在
        raise HTTPException(
            # 使用 HTTP 404
            status_code=status.HTTP_404_NOT_FOUND,
            # 返回清晰但稳定的错误消息
            detail="工单不存在",
        )

    # 把 ORM 对象转换成 API 响应
    return to_ticket_response(ticket_record)


# 根据工单 ID 修改数据库中的工单状态
@app.patch(
    # ticket_id 来自 URL 路径
    "/v1/tickets/{ticket_id}/status",
    # 声明成功响应结构
    response_model=TicketResponse,
    # 在 Swagger 中归入 tickets
    tags=["tickets"],
)
def update_ticket_status(
        # 接收客户端路径中的工单 ID
        ticket_id: str,
        # 接收并验证客户端提交的新状态
        request: TicketStatusUpdateRequest,
        # 获取本次请求独享的数据库 Session
        session: DatabaseSession,
        # 修改成功后返回工单响应
) -> TicketResponse:
    # 捕获根据 ID 查询时的数据库错误
    try:
        # 使用 Repository 查询目标 ORM 工单
        ticket_record = (
            ticket_repository.get_ticket_by_id(
                # 传入当前请求的 Session
                session,
                # 传入目标工单 ID
                ticket_id,
            )
        )

    # 捕获 Repository 转换后的数据库异常
    except RuntimeError as error:
        # 转换成 HTTP 500
        raise HTTPException(
            # 返回内部服务错误状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 不向客户端暴露数据库细节
            detail="工单服务暂时不可用",
            # 保留后台异常链
        ) from error

    # None 表示数据库中不存在目标工单
    if ticket_record is None:
        # 返回资源不存在错误
        raise HTTPException(
            # 使用 HTTP 404
            status_code=status.HTTP_404_NOT_FOUND,
            # 返回客户端可理解的信息
            detail="工单不存在",
        )

    # 捕获状态修改和事务提交错误
    try:
        # 调用 Repository 修改状态并提交事务
        updated_ticket = (
            ticket_repository.update_ticket_status(
                # 传入当前 Session
                session,
                # 传入已经查询到的 ORM 工单
                ticket_record,
                # 传入经过 Pydantic 验证的新状态
                request.status,
            )
        )

    # 捕获 Repository 修改失败异常
    except RuntimeError as error:
        # 转换成统一 HTTP 500
        raise HTTPException(
            # 设置响应状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 隐藏数据库内部错误
            detail="工单服务暂时不可用",
            # 保留异常链
        ) from error

    # 将更新后的 ORM 工单转换成响应 DTO
    return to_ticket_response(
        updated_ticket
    )

# 根据工单 ID 修改或清空当前处理人
@app.patch(
    # ticket_id 来自 URL 路径
    "/v1/tickets/{ticket_id}/assignee",
    # 声明成功响应使用 TicketResponse
    response_model=TicketResponse,
    # 在 Swagger 文档中归入 tickets 分组
    tags=["tickets"],
)
def update_ticket_assignee(
        # 接收 URL 中的工单 ID
        ticket_id: str,
        # 接收并验证客户端提交的处理人
        request: TicketAssigneeUpdateRequest,
        # 获取当前请求独享的数据库 Session
        session: DatabaseSession,
        # 修改成功后返回完整工单
) -> TicketResponse:
    # 捕获查询工单时可能发生的数据库错误
    try:
        # 根据主键查询目标工单
        ticket_record = (
            ticket_repository.get_ticket_by_id(
                # 传入当前请求的 Session
                session,
                # 传入 URL 中的工单 ID
                ticket_id,
            )
        )

    # 捕获 Repository 转换后的数据库异常
    except RuntimeError as error:
        # 转换成统一的 HTTP 500
        raise HTTPException(
            # 返回服务器内部错误状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 不向客户端泄漏数据库异常细节
            detail="工单服务暂时不可用",
        ) from error

    # Repository 返回 None 表示目标工单不存在
    if ticket_record is None:
        # 返回资源不存在错误
        raise HTTPException(
            # 使用 HTTP 404
            status_code=status.HTTP_404_NOT_FOUND,
            # 返回客户端可理解的错误信息
            detail="工单不存在",
        )

    # 捕获修改处理人和提交事务时的数据库错误
    try:
        # 调用 Repository 保存新的处理人
        updated_ticket = (
            ticket_repository.update_ticket_assignee(
                # 传入当前请求的 Session
                session,
                # 传入已经查询到的 ORM 工单
                ticket_record,
                # 字符串表示分配；None 表示取消分配
                request.assigned_to,
            )
        )

    # 捕获 Repository 转换后的修改失败异常
    except RuntimeError as error:
        # 转换成稳定的 HTTP 500 响应
        raise HTTPException(
            # 返回服务器内部错误状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 隐藏底层数据库错误
            detail="工单服务暂时不可用",
        ) from error

    # 将更新后的 ORM 工单转换成 API 响应
    return to_ticket_response(
        updated_ticket
    )

# 根据工单 ID 删除数据库中的工单
@app.delete(
    # ticket_id 来自 URL 路径
    "/v1/tickets/{ticket_id}",
    # 删除成功不返回响应正文
    status_code=status.HTTP_204_NO_CONTENT,
    # 在 Swagger 中归入 tickets
    tags=["tickets"],
)
def delete_ticket(
        # 接收客户端请求的工单 ID
        ticket_id: str,
        # 获取本次请求独享的 Session
        session: DatabaseSession,
        # 删除成功不返回对象
) -> None:
    # 捕获主键查询错误
    try:
        # 查询需要删除的 ORM 工单
        ticket_record = (
            ticket_repository.get_ticket_by_id(
                # 传入当前 Session
                session,
                # 传入目标 ID
                ticket_id,
            )
        )

    # 捕获 Repository 数据库错误
    except RuntimeError as error:
        # 转换成 HTTP 500
        raise HTTPException(
            # 设置状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 隐藏数据库内部错误
            detail="工单服务暂时不可用",
            # 保留异常链
        ) from error

    # 查询结果为 None 表示工单不存在
    if ticket_record is None:
        # 返回 HTTP 404
        raise HTTPException(
            # 设置不存在状态码
            status_code=status.HTTP_404_NOT_FOUND,
            # 设置错误信息
            detail="工单不存在",
        )

    # 捕获删除和事务提交错误
    try:
        # 调用 Repository 删除 ORM 工单
        ticket_repository.delete_ticket(
            # 传入当前 Session
            session,
            # 传入需要删除的 ORM 对象
            ticket_record,
        )

    # 捕获 Repository 删除失败
    except RuntimeError as error:
        # 转换成 HTTP 500
        raise HTTPException(
            # 设置状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 设置统一客户端错误
            detail="工单服务暂时不可用",
            # 保留异常链
        ) from error


# 验证请求并把新工单保存到数据库
@app.post(
    # 定义创建工单的接口地址
    "/v1/tickets",
    # 声明成功响应必须符合 TicketResponse
    response_model=TicketResponse,
    # 创建资源成功返回 HTTP 201
    status_code=status.HTTP_201_CREATED,
    # 在 Swagger 文档中归入 tickets
    tags=["tickets"],
)
def create_ticket(
        # 接收并验证客户端提交的 JSON 请求体
        request: TicketCreateRequest,
        # 获取本次请求独享的数据库 Session
        session: DatabaseSession,
        # 创建成功后返回工单响应
) -> TicketResponse:
    # 把经过验证的请求 DTO 转换成 ORM 数据库对象
    ticket_record = TicketRecord(
        # 使用 UUID 为新工单生成唯一 ID
        id=str(uuid4()),
        # 从请求模型取得客户姓名
        customer_name=request.customer_name,
        # EmailStr 转换为数据库需要保存的普通字符串
        contact_email=str(
            request.contact_email
        ),
        # 从请求模型取得工单渠道
        channel=request.channel,
        # 从请求模型取得工单标题
        subject=request.subject,
        # 从请求模型取得详细描述
        description=request.description,
        # 从请求模型取得优先级
        priority=request.priority,
        # 新工单明确使用 open 初始状态
        status="open",
    )

    # 捕获 Repository 转换后的数据库异常
    try:
        # 通过 Repository 把 ORM 对象写入数据库
        created_ticket = (
            ticket_repository.create_ticket(
                # 传入当前请求的 Session
                session,
                # 传入准备保存的 ORM 对象
                ticket_record,
            )
        )

    # 捕获 Repository 抛出的稳定 RuntimeError
    except RuntimeError as error:
        # 将数据库错误转换为 HTTP 500
        raise HTTPException(
            # 使用内部服务错误状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # 不向客户端泄漏 SQL 或数据库内部信息
            detail="工单服务暂时不可用",
            # 保留后台排查所需的异常链
        ) from error

    # 将保存后的 ORM 对象转换成 API 响应模型
    return to_ticket_response(
        created_ticket
    )
