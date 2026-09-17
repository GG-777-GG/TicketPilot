# Annotated 用来组合类型和 FastAPI 依赖信息
from typing import Annotated

# Callable 用来标注“一个可以被调用的函数”
from collections.abc import Callable

# 导入系统允许的用户角色类型
from ticketpilot.identity.types import UserRole

# 导入 FastAPI 的依赖注入和 HTTP 异常工具
from fastapi import (
    # Depends 表示参数由 FastAPI 自动提供
    Depends,

    # HTTPException 用来返回 HTTP 错误
    HTTPException,

    # status 提供带名称的状态码
    status,
)

# 导入 Bearer Token 请求头解析工具
from fastapi.security import (
    # 保存解析后的认证方案和令牌
    HTTPAuthorizationCredentials,

    # 自动读取 Authorization 请求头
    HTTPBearer,
)

# 导入用户 Repository
from ticketpilot.identity import repository as user_repository

# 导入多个路由共用的数据库 Session 依赖类型
from ticketpilot.core.dependencies import DatabaseSession

# 导入用户 ORM 模型
from ticketpilot.identity.models import UserRecord

# 导入 JWT 验证函数和异常
from ticketpilot.identity.security import (
    InvalidAccessTokenError,
    decode_access_token,
)


# 创建 Bearer Token 请求头解析器
bearer_scheme = HTTPBearer(
    # 缺少令牌时先返回 None
    # 后面由我们统一生成中文错误
    auto_error=False,
)


# 定义 Bearer 请求头依赖类型
BearerCredentials = Annotated[
    # 请求可能带有认证信息，也可能没有
    HTTPAuthorizationCredentials | None,

    # FastAPI 自动调用 bearer_scheme
    Depends(bearer_scheme),
]


# 创建统一的身份认证错误
def authentication_error() -> HTTPException:
    # 返回一个新的 HTTP 401 异常
    return HTTPException(
        # 401 表示没有通过身份认证
        status_code=status.HTTP_401_UNAUTHORIZED,

        # 不泄露令牌的具体错误原因
        detail="无效或缺少访问令牌",

        # 告诉客户端应该使用 Bearer Token
        headers={
            "WWW-Authenticate": "Bearer",
        },
    )


# 根据 Bearer Token 获取当前登录用户
def get_current_user(
    # 从 Authorization 请求头中取得认证信息
    credentials: BearerCredentials,

    # 获取本次请求独享的数据库 Session
    session: DatabaseSession,
) -> UserRecord:
    # 如果客户端没有发送认证信息
    if credentials is None:
        # 返回统一的 HTTP 401
        raise authentication_error()

    # 确认客户端使用的是 Bearer 认证方式
    if credentials.scheme.lower() != "bearer":
        # 不接受其他认证方式
        raise authentication_error()

    # 尝试验证 JWT 并取得用户 ID
    try:
        # credentials.credentials 是 Bearer 后面的 JWT
        user_id = decode_access_token(
            credentials.credentials,
        )

    # 捕获无效、篡改或者过期的 JWT
    except InvalidAccessTokenError as error:
        # 转换成统一的 HTTP 401
        raise authentication_error() from error

    # 根据 JWT 中的用户 ID 查询数据库
    try:
        # 确认用户现在仍然存在
        user = user_repository.get_user_by_id(
            session,
            user_id,
        )

    # 捕获 Repository 转换后的数据库错误
    except RuntimeError as error:
        # 数据库故障应该返回 HTTP 500
        raise HTTPException(
            # 表示服务器内部发生错误
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),

            # 不向客户端泄露数据库细节
            detail="用户服务暂时不可用",
        ) from error

    # JWT 有效，但对应用户可能已经被删除
    if user is None:
        # 此 JWT 不能再代表有效用户
        raise authentication_error()

    # 检查用户账号是否已经被停用
    if not user.is_active:
        # 身份有效，但账号不允许继续使用
        raise HTTPException(
            # 403 表示已经确认身份，但被禁止访问
            status_code=status.HTTP_403_FORBIDDEN,

            # 返回账号状态说明
            detail="用户账号已停用",
        )

    # 所有检查通过，返回数据库用户
    return user


# 定义供 API 路由复用的当前用户类型
CurrentUser = Annotated[
    # 路由最终得到 UserRecord
    UserRecord,

    # FastAPI 自动执行 get_current_user
    Depends(get_current_user),
]

# 创建一个可以限制用户角色的依赖
def require_roles(
    # 接收允许访问接口的角色
    *allowed_roles: UserRole,
) -> Callable[[UserRecord], UserRecord]:

    # 这个内部函数会在每次请求时执行
    def check_role(
        # 先验证 JWT，并取得当前登录用户
        current_user: CurrentUser,
    ) -> UserRecord:

        # 判断当前用户的角色是否在允许列表中
        if current_user.role not in allowed_roles:
            # 用户已经登录，但没有足够权限
            raise HTTPException(
                # 403 表示身份已确认，但是禁止访问
                status_code=status.HTTP_403_FORBIDDEN,

                # 返回给客户端的错误信息
                detail="权限不足",
            )

        # 权限检查通过，返回当前用户
        return current_user

    # 返回真正供 FastAPI 执行的权限检查函数
    return check_role

# 定义“主管或管理员用户”的依赖类型
SupervisorOrAdminUser = Annotated[
    # 路由最终接收到的实际对象是 UserRecord
    UserRecord,

    # FastAPI 在进入路由前执行角色权限检查
    Depends(
        # 创建只允许主管和管理员通过的检查函数
        require_roles(
            "supervisor",
            "admin",
        )
    ),
]
