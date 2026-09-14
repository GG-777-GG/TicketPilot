# 导入 Annotated，用于组合类型和 FastAPI 依赖信息
import uuid
from typing import Annotated

# 让 FastAPI 验证 JWT，并提供当前登录用户
from  ticketpilot.auth import CurrentUser

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
    # 定义并验证 URL 查询参数
    Query,
)

# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入 Repository，负责数据库 CRUD
from ticketpilot import repository as ticket_repository

# 导入用户 Repository
from ticketpilot import user_repository

# 导入每个请求使用的 Session 依赖
from ticketpilot.database import get_session

# 导入认证需要的安全函数
from ticketpilot.security import (
    # 登录成功后生成 JWT
    create_access_token,

    # 注册时生成密码哈希
    hash_password,

    # 登录时验证密码
    verify_password,
)


# 导入数据库工单 ORM 模型
from ticketpilot.db_models import (
    TicketRecord,
    UserRecord,
)

# 导入 API 请求和响应模型
from ticketpilot.schemas import (
    # 登录成功的 JWT 响应
    AccessTokenResponse,

    TicketAssigneeUpdateRequest,
    TicketCreateRequest,
    TicketResponse,
    TicketStatusUpdateRequest,

    # 注册请求
    UserCreateRequest,

    # 登录请求
    UserLoginRequest,

    # 用户信息响应
    UserResponse,
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

# 将用户 ORM 对象转换成安全的 API 响应
def to_user_response(
    # 接收数据库中的用户 ORM 对象
    user: UserRecord,
) -> UserResponse:
    # 创建 Pydantic 用户响应
    return UserResponse(
        # 返回用户 ID
        id=user.id,

        # 返回登录邮箱
        email=user.email,

        # 返回显示名称
        display_name=user.display_name,

        # 返回当前角色
        role=user.role,

        # 返回账号启用状态
        is_active=user.is_active,

        # 返回账号创建时间
        created_at=user.created_at,
    )


# 返回服务当前的基础健康状态
@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "TicketPilot"
    }

# 注册一名新的普通用户
@app.post(
    # 定义注册接口地址
    "/v1/auth/register",

    # 声明成功响应使用 UserResponse
    response_model=UserResponse,

    # 成功创建用户返回 HTTP 201
    status_code=status.HTTP_201_CREATED,

    # 在 Swagger 文档中归入 auth 分组
    tags=["auth"],
)
def register_user(
    # 接收并验证客户端提交的注册数据
    request: UserCreateRequest,

    # 获取本次请求独享的数据库 Session
    session: DatabaseSession,
) -> UserResponse:
    # 从 SecretStr 中取得用户输入的真实密码
    plain_password = (
        request.password.get_secret_value()
    )

    # 使用 Argon2 生成密码哈希
    password_hash = hash_password(
        plain_password
    )

    # 构造准备写入数据库的用户 ORM 对象
    user_record = UserRecord(
        # 生成用户唯一 UUID
        id=str(uuid4()),

        # EmailStr 转换成普通字符串
        # Repository 还会统一转换成小写
        email=str(request.email),

        # 保存已经清理过空格的显示名称
        display_name=request.display_name,

        # 数据库只保存密码哈希
        password_hash=password_hash,
    )

    # 捕获用户数据库操作异常
    try:
        # 通过 Repository 保存用户
        created_user = user_repository.create_user(
            session,
            user_record,
        )

    # 捕获邮箱唯一约束冲突
    except user_repository.UserAlreadyExistsError as error:
        # 转换成 HTTP 409 Conflict
        raise HTTPException(
            # 表示请求与当前数据状态冲突
            status_code=status.HTTP_409_CONFLICT,

            # 返回客户端可以理解的错误
            detail="用户邮箱已存在",
        ) from error

    # 捕获其他数据库错误
    except RuntimeError as error:
        # 转换成统一的 HTTP 500
        raise HTTPException(
            # 设置服务器内部错误状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,

            # 不向客户端泄漏数据库错误细节
            detail="用户服务暂时不可用",
        ) from error

    # 返回不包含密码哈希的安全响应
    return to_user_response(
        created_user
    )

# 使用邮箱和密码登录
@app.post(
    # 定义登录接口地址
    "/v1/auth/login",

    # 登录成功返回 JWT 响应模型
    response_model=AccessTokenResponse,

    # 登录接口归入 Swagger 的 auth 分组
    tags=["auth"],
)

def login_user(
    # 接收经过 Pydantic 验证的登录数据
    request: UserLoginRequest,

    # 获取本次请求独享的数据库 Session
    session: DatabaseSession,
) -> AccessTokenResponse:
    # 尝试从数据库查询用户
    try:
        # 根据邮箱查找用户
        # Repository 会自动把邮箱转换成小写
        user = user_repository.get_user_by_email(
            session,
            str(request.email),
        )

    # 捕获 Repository 转换后的数据库异常
    except RuntimeError as error:
        # 向客户端返回统一的服务器错误
        raise HTTPException(
            # 表示服务器内部出现问题
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),

            # 不泄露数据库内部错误
            detail="用户服务暂时不可用",
        ) from error

    # 如果数据库中没有这个邮箱
    if user is None:
        # 返回未通过身份认证
        raise HTTPException(
            # 401 表示身份认证失败
            status_code=status.HTTP_401_UNAUTHORIZED,

            # 不明确告诉别人到底是邮箱还是密码错误
            detail="邮箱或密码错误",

            # 告诉客户端本系统使用 Bearer Token
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    # 从 SecretStr 中取得用户输入的真实密码
    plain_password = (
        request.password.get_secret_value()
    )

    # 验证明文密码是否匹配数据库密码哈希
    password_is_valid = verify_password(
        plain_password,
        user.password_hash,
    )

    # 如果密码验证失败
    if not password_is_valid:
        # 与用户不存在返回完全相同的信息
        raise HTTPException(
            # 密码错误也是身份认证失败
            status_code=status.HTTP_401_UNAUTHORIZED,

            # 避免泄露账号是否存在
            detail="邮箱或密码错误",

            # 声明认证方式
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    # 密码正确后，再检查账号是否处于启用状态
    if not user.is_active:
        # 账号存在、密码正确，但不允许使用
        raise HTTPException(
            # 403 表示身份已经确认，但没有使用权限
            status_code=status.HTTP_403_FORBIDDEN,

            # 告诉用户账号目前不可用
            detail="用户账号已停用",
        )

    # 使用用户 ID 生成 JWT
    access_token = create_access_token(
        user.id
    )

    # 返回登录令牌
    return AccessTokenResponse(
        # 客户端后续请求需要携带这个 JWT
        access_token=access_token,

        # 标准 Bearer Token 类型
        token_type="bearer",
    )

# 查询当前登录用户的信息
@app.get(
    # me 表示当前请求的用户自己
    "/v1/auth/me",

    # 只允许返回 UserResponse 中声明的用户信息
    response_model=UserResponse,

    # 在接口文档中归入 auth 分组
    tags=["auth"],
)
def get_me(
    # FastAPI 先执行 get_current_user()
    # 验证通过后，把查到的 UserRecord 传入这个参数
    current_user: CurrentUser,
) -> UserResponse:
    # 转换成安全响应，不返回密码或密码哈希
    return to_user_response(
        current_user,
    )

# 分页查询数据库中的工单
@app.get(
    # 定义接口地址
    "/v1/tickets",

    # 声明响应仍然是 TicketResponse 列表
    response_model=list[TicketResponse],

    # 在 Swagger 文档中归入 tickets 分组
    tags=["tickets"],
)
def list_tickets(
    # 由 FastAPI 为本次请求注入数据库 Session
    session: DatabaseSession,

    # 必须通过身份认证，才能执行创建工单的代码
    current_user: CurrentUser,

    # 接收 URL 中的 limit 查询参数
    limit: Annotated[
        # 参数必须是整数
        int,

        # 最小为 1，最大为 100
        Query(
            ge=1,
            le=100,
        ),
    # 客户端没有提供时，默认查询 20 条
    ] = 20,

    # 接收 URL 中的 offset 查询参数
    offset: Annotated[
        # 参数必须是整数
        int,

        # offset 不能小于 0
        Query(
            ge=0,
        ),
    # 客户端没有提供时，默认不跳过数据
    ] = 0,

    # 返回分页后的工单列表
) -> list[TicketResponse]:
    # 捕获 Repository 查询时的数据库异常
    try:
        # 调用 Repository 查询指定的一页工单
        ticket_records = ticket_repository.list_tickets(
            # 传入当前请求的数据库 Session
            session,

            # 传入本次最多返回的数量
            limit=limit,

            # 传入需要跳过的数量
            offset=offset,
        )

    # 捕获 Repository 转换后的数据库异常
    except RuntimeError as error:
        # 转换成统一的 HTTP 500
        raise HTTPException(
            # 设置服务器内部错误状态码
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,

            # 不向客户端泄漏数据库错误细节
            detail="工单服务暂时不可用",
        ) from error

    # 将查询到的 ORM 工单逐个转换成 API 响应
    return [
        # 转换当前 ORM 工单
        to_ticket_response(ticket_record)

        # 遍历 Repository 返回的当前页数据
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
        # 必须通过身份认证，才能执行创建工单的代码
        current_user: CurrentUser,

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
        # 必须通过身份认证，才能执行创建工单的代码
        current_user: CurrentUser,

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
        # 必须通过身份认证，才能执行创建工单的代码
        current_user: CurrentUser,

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
        # 必须通过身份认证，才能执行创建工单的代码
        current_user: CurrentUser,

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
        # 自动验证 JWT，并取得当前登录用户
        current_user: CurrentUser,
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
