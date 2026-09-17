"""注册、登录和当前用户接口的数据结构。"""

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)

from ticketpilot.identity.types import UserRole


class UserCreateRequest(BaseModel):
    """注册用户时允许提交的数据。"""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("display_name", mode="before")
    @classmethod
    def strip_display_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class UserLoginRequest(BaseModel):
    """登录时允许提交的数据。"""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class AccessTokenResponse(BaseModel):
    """登录成功后返回的 Bearer Token。"""

    access_token: str = Field(min_length=1)
    token_type: Literal["bearer"] = "bearer"


class UserResponse(BaseModel):
    """可以安全返回给客户端的用户信息。"""

    id: str
    email: EmailStr
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


__all__ = [
    "AccessTokenResponse",
    "UserCreateRequest",
    "UserLoginRequest",
    "UserResponse",
    "UserRole",
]
