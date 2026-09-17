"""工单 API 的请求与响应数据结构。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ticketpilot.tickets.types import Channel, Priority, TicketStatus


class TicketCreateRequest(BaseModel):
    """创建工单时允许客户端提交的数据。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    customer_name: str = Field(min_length=1, max_length=100)
    contact_email: EmailStr
    channel: Channel
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    priority: Priority = "medium"


class TicketStatusUpdateRequest(BaseModel):
    """修改工单状态时允许提交的数据。"""

    model_config = ConfigDict(extra="forbid")

    status: TicketStatus


class TicketAssigneeUpdateRequest(BaseModel):
    """分配或取消分配工单处理人时允许提交的数据。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    assigned_to: str | None = Field(min_length=1, max_length=100)


class TicketResponse(BaseModel):
    """工单接口返回给客户端的数据。"""

    id: str
    customer_name: str
    contact_email: EmailStr
    channel: Channel
    subject: str
    description: str
    priority: Priority
    status: TicketStatus
    assigned_to: str | None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "Channel",
    "Priority",
    "TicketAssigneeUpdateRequest",
    "TicketCreateRequest",
    "TicketResponse",
    "TicketStatus",
    "TicketStatusUpdateRequest",
]
