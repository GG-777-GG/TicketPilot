
# 导入 Python 的日期时间类型，用于描述 API 中的时间数据
from datetime import datetime

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field
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
