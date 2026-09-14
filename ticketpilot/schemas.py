
# 导入 Python 的日期时间类型，用于描述 API 中的时间数据
from datetime import datetime

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,

    # 用于安全隐藏密码
    SecretStr,

    # 用于单独清理显示名称
    field_validator,
)

Channel = Literal[
    "web",
    "phone",
    "email",
    "chat",
]

Priority = Literal[
    "low",
    "medium",
    "high",
]

TicketStatus = Literal[
    "open",
    "in_progress",
    "resolved",
    "closed",
]
UserRole = Literal[
    # 普通客服
    "agent",

        # 客服主管
    "supervisor",

        # 系统管理员
    "admin",
]


# 定义创建工单接口允许接收的请求数据
class TicketCreateRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    customer_name: str = Field(
        min_length=1,
        max_length=100,
    )
    contact_email: EmailStr
    channel: Channel
    subject: str = Field(
        min_length=1,
        max_length=200,
    )
    description: str = Field(
        min_length=1,
        max_length=4000,
    )
    priority: Priority = "medium"


# 定义修改工单状态接口允许接收的请求数据
class TicketStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    status: TicketStatus


# 定义修改工单处理人接口允许接收的数据
class TicketAssigneeUpdateRequest(BaseModel):
    # 设置字符串清理和额外字段检查规则
    model_config = ConfigDict(
        # 自动删除处理人姓名两端的空格
        str_strip_whitespace=True,
        # 拒绝请求模型中没有声明的字段
        extra="forbid",
    )

    # 请求必须提供 assigned_to，但值可以是字符串或 None
    assigned_to: str | None = Field(
        # 字符串至少包含一个字符
        min_length=1,
        # 与数据库 String(100) 的长度保持一致
        max_length=100,
    )

# 定义注册接口允许接收的数据
class UserCreateRequest(BaseModel):
    # 拒绝模型中没有声明的字段
    model_config = ConfigDict(
        extra="forbid",
    )

    # 用户登录邮箱
    email: EmailStr

    # 用户显示名称
    display_name: str = Field(
        # 去掉空格后至少要有一个字符
        min_length=1,

        # 与数据库 String(100) 保持一致
        max_length=100,
    )

    # 用户登录密码
    password: SecretStr = Field(
        # 密码至少 8 个字符
        min_length=8,

        # 限制超长密码请求
        max_length=128,
    )

    # 在其他验证规则执行前清理显示名称
    @field_validator(
        "display_name",
        mode="before",
    )
    @classmethod
    def strip_display_name(
        # cls 表示当前 Pydantic 模型类
        cls,

        # 接收客户端传入的原始值
        value: object,
    ) -> object:
        # 只有字符串才需要删除两端空格
        if isinstance(
            value,
            str,
        ):
            # 返回清理后的显示名称
            return value.strip()

        # 非字符串交给 Pydantic 继续验证
        return value

# 定义登录接口允许接收的数据
class UserLoginRequest(BaseModel):
    # 拒绝客户端提交没有声明的额外字段
    model_config = ConfigDict(
        extra="forbid",
    )

    # 用户使用邮箱登录
    email: EmailStr

    # 使用 SecretStr 避免密码被日志直接显示
    password: SecretStr = Field(
        # 登录密码不能为空
        min_length=1,

        # 防止客户端提交异常大的密码数据
        max_length=128,
    )


# 定义登录成功后返回的访问令牌
class AccessTokenResponse(BaseModel):
    # JWT 访问令牌字符串
    access_token: str = Field(
        min_length=1,
    )

    # 告诉客户端应该使用 Bearer 身份验证方式
    token_type: Literal["bearer"] = "bearer"

# 定义工单接口允许返回给客户端的数据
class TicketResponse(BaseModel):
    id: str
    customer_name: str
    contact_email: EmailStr
    channel: Channel
    subject: str
    description: str
    priority: Priority
    status: TicketStatus
    # 返回当前处理人；None 表示尚未分配
    assigned_to: str | None

    # 返回工单在数据库中的创建时间
    created_at: datetime

    # 返回工单最后一次被修改的时间
    updated_at: datetime

# 定义允许返回给客户端的用户信息
class UserResponse(BaseModel):
    # 用户唯一编号
    id: str

    # 用户登录邮箱
    email: EmailStr

    # 用户显示名称
    display_name: str

    # 用户当前角色
    role: UserRole

    # 账号当前是否启用
    is_active: bool

    # 账号创建时间
    created_at: datetime