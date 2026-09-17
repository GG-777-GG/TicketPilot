"""身份模块的 HTTP 路由。

这里只处理 HTTP 输入、状态码和响应转换；用户数据访问由 repository 负责，
密码与 JWT 由 security 负责。
"""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from ticketpilot.core.dependencies import DatabaseSession
from ticketpilot.identity import repository as user_repository
from ticketpilot.identity.dependencies import CurrentUser
from ticketpilot.identity.models import UserRecord
from ticketpilot.identity.schemas import (
    AccessTokenResponse,
    UserCreateRequest,
    UserLoginRequest,
    UserResponse,
)
from ticketpilot.identity.security import (
    create_access_token,
    hash_password,
    verify_password,
)


router = APIRouter(
    prefix="/v1/auth",
    tags=["auth"],
)


def to_user_response(user: UserRecord) -> UserResponse:
    """把包含密码哈希的 ORM 用户转换成安全响应。"""

    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    request: UserCreateRequest,
    session: DatabaseSession,
) -> UserResponse:
    """注册一名默认角色为 agent 的新用户。"""

    user_record = UserRecord(
        id=str(uuid4()),
        email=str(request.email),
        display_name=request.display_name,
        password_hash=hash_password(
            request.password.get_secret_value(),
        ),
    )

    try:
        created_user = user_repository.create_user(
            session,
            user_record,
        )
    except user_repository.UserAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户邮箱已存在",
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="用户服务暂时不可用",
        ) from error

    return to_user_response(created_user)


@router.post(
    "/login",
    response_model=AccessTokenResponse,
)
def login_user(
    request: UserLoginRequest,
    session: DatabaseSession,
) -> AccessTokenResponse:
    """验证邮箱和密码，成功后签发 JWT。"""

    try:
        user = user_repository.get_user_by_email(
            session,
            str(request.email),
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="用户服务暂时不可用",
        ) from error

    if user is None or not verify_password(
        request.password.get_secret_value(),
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户账号已停用",
        )

    return AccessTokenResponse(
        access_token=create_access_token(user.id),
        token_type="bearer",
    )


@router.get(
    "/me",
    response_model=UserResponse,
)
def get_me(current_user: CurrentUser) -> UserResponse:
    """返回当前 JWT 所代表的用户。"""

    return to_user_response(current_user)
