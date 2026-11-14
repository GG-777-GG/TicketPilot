"""
文件职责：定义审批接口的请求与响应数据结构。
作用与配合：后续由审批路由接收请求，服务层处理业务，
再使用这些模型返回结果。
边界：不访问数据库、不验证登录身份，也不执行工单操作。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
)


# 审批申请从提交到执行结束，可能处于的状态。
ApprovalStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "executing",
    "succeeded",
    "failed",
]


class ApprovalCreateRequest(BaseModel):
    """用户提交一项需要审批的工单操作。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    # 必须符合 UUID 格式；工单是否存在由服务层检查。
    ticket_id: UUID

    # 第一版只支持申请关闭工单。
    action: Literal["close_ticket"] = "close_ticket"

    # 申请人说明为什么需要执行这项操作。
    request_reason: str = Field(
        min_length=1,
        max_length=500,
    )


class ApprovalReviewRequest(BaseModel):
    """主管或管理员提交审批决定及理由。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    # 只接受真正的布尔值。
    approved: StrictBool

    reason: str = Field(
        min_length=1,
        max_length=500,
    )


class ApprovalResponse(BaseModel):
    """向客户端返回审批申请、处理状态和执行结果。"""

    # 审批申请编号。
    id: UUID

    # 实际业务操作编号，失败重试时保持不变。
    operation_id: UUID

    ticket_id: UUID
    action: Literal["close_ticket"]
    status: ApprovalStatus

    # 身份信息由后端根据登录用户记录。
    requested_by: UUID
    reviewed_by: UUID | None

    request_reason: str

    # 尚未审批时没有审批理由。
    approval_reason: str | None

    # 尚未产生执行结果时为 None。
    result: str | None

    created_at: datetime
    updated_at: datetime

    # 尚未审批时没有审批时间。
    reviewed_at: datetime | None

class ApprovalWorkflowResponse(BaseModel):
    """返回审批实时数据，以及对应工作流的检查点信息。"""

    # 当前业务数据库中的审批记录。
    approval: ApprovalResponse

    # 后端根据已有操作编号确定会话编号。
    thread_id: str

    # 是否已经保存过这条流程的状态。
    started: bool

    # 最近检查点中的审批状态，可能落后于业务数据库。
    checkpoint_status: ApprovalStatus | None

    # 检查点记录的待执行节点。
    next_nodes: list[str]

    waiting_for_approval: bool

    # 只返回是否出错，不公开内部异常内容。
    has_error: bool

class ApprovalWorkflowRunRequest(BaseModel):
    """指定工作流操作；身份、目标和操作编号均由后端取得。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["start", "resume", "retry"]