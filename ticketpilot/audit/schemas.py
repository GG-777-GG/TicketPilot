"""
文件职责：定义审计写入白名单、只读查询条件和公开查询响应。
作用与配合：业务服务构造事件，查询Router/Service使用独立请求与响应模型。
边界：AuditEventInput不是公开HTTP写入接口，不接受任意details或异常正文。
"""

from typing import Literal
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditEventInput(BaseModel):
    """校验一条由可信后端创建的审计事件。"""

    model_config = ConfigDict(extra="forbid")

    event_type: Literal[
        "approval.requested",
        "approval.approved",
        "approval.rejected",
        "ticket.closed",
        "execution.replayed",
        "approval.denied",
        "approval.failed",
    ]

    # 来自认证用户；无法确认身份时允许为空。
    actor_id: UUID | None = None

    # 保存发生操作时的角色快照。
    actor_role: Literal[
        "agent", "supervisor", "admin", "unknown"
    ] | None = None

    approval_id: UUID | None = None
    ticket_id: UUID | None = None
    operation_id: UUID | None = None

    outcome: Literal[
        "succeeded", "denied", "failed", "replayed"
    ]

    # 只记录稳定错误类别，不存原始异常。
    error_code: Literal[
        "permission_denied",
        "not_found",
        "state_conflict",
        "storage_error",
        "workflow_error",
    ] | None = None

    request_id: UUID | None = None

    # 后续从OpenTelemetry取得；当前可以为空。
    trace_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )


class AuditEventQuery(BaseModel):
    """管理查询条件；至少一个目标编号的业务约束由Service检查。"""

    model_config = ConfigDict(extra="forbid")

    approval_id: UUID | None = None
    operation_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class AuditEventResponse(AuditEventInput):
    """公开既有白名单字段及数据库生成的事件编号和记录时间。"""

    id: UUID
    occurred_at: datetime


class AuditEventPage(BaseModel):
    """一页事件和翻页信息，不额外计算整个历史的总数。"""

    items: list[AuditEventResponse]
    limit: int
    offset: int
    has_more: bool
