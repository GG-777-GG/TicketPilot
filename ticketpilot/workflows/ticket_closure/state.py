"""
文件职责：定义关闭工单流程的State与运行Context。
作用与配合：State由检查点保存，Context为节点提供本次Session和当前用户。
使用位置：由nodes.py、graph.py及调用入口使用。
边界：只定义类型，不查询数据库、不组装工作流。
"""

from dataclasses import dataclass
from typing import TypedDict

from sqlalchemy.orm import Session

from ticketpilot.approvals.schemas import ApprovalStatus
from ticketpilot.identity.models import UserRecord


class TicketClosureState(TypedDict):
    """保存本次流程需要的数据，不保存数据库连接或用户对象。"""

    # 本次流程处理哪一份审批申请。
    approval_id: str

    # 从审批记录读取；沿用已有编号，不在节点里重新生成。
    operation_id: str

    # 操作目标也从审批记录读取。
    ticket_id: str

    # 尚未读取时为 None；读取后保存数据库中的状态。
    approval_status: ApprovalStatus | None

    # 尚未执行时为空字符串。
    result: str


@dataclass(frozen=True)
class TicketClosureContext:
    """提供本次运行的数据库会话、用户和执行范围。"""

    session: Session
    viewer: UserRecord

    # 普通管理入口保持原行为；Agent申请工具明确传False。
    allow_execution: bool = True