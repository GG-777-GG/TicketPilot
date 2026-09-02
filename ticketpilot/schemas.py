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
