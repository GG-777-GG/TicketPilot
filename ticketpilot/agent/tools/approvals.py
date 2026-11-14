"""
文件职责：为Agent提供申请关闭工单的受控工具。
作用与配合：复用审批创建服务和工作流启动服务。
使用位置：由Dispatcher传入已验证参数、原始用户授权和认证用户。
边界：不审核申请、不执行关闭、不从模型参数读取用户身份。
"""

from pathlib import Path
import re

from pydantic import ValidationError

from sqlalchemy.orm import Session

from ticketpilot.agent.schemas import (
    RequestTicketClosureArguments,
    RequestTicketClosureResult,
)
from ticketpilot.approvals import service
from ticketpilot.approvals.schemas import ApprovalCreateRequest
from ticketpilot.identity.models import UserRecord
from ticketpilot.workflows.ticket_closure.operations import run_workflow


def parse_closure_request(message: str) -> RequestTicketClosureArguments | None:
    """从原始用户明确指令取得授权目标；演示版采用保守的完整句式。"""
    matched = re.fullmatch(
        r"\s*请为工单\s+([0-9a-fA-F-]{36})\s+提交关闭申请[，,]\s*原因[：:]\s*(.{1,500})",
        message, flags=re.DOTALL,
    )
    if matched is None:
        return None
    try:
        return RequestTicketClosureArguments(
            ticket_id=matched.group(1), request_reason=matched.group(2),
        )
    except ValidationError:
        return None


def request_ticket_closure(
    session: Session,
    arguments: RequestTicketClosureArguments,
    *,
    requester: UserRecord,
    checkpoint_path: Path,
) -> RequestTicketClosureResult:
    """保存申请，再尝试启动流程；启动失败也保留原申请编号。"""

    record = service.create_approval(
        session=session,
        request=ApprovalCreateRequest(
            ticket_id=arguments.ticket_id,
            request_reason=arguments.request_reason,
        ),
        requester=requester,
    )

    # 创建服务返回前已经提交。
    # 先保存普通值，后续工作流可能刷新或回滚当前Session。
    approval_id = record.id
    operation_id = record.operation_id
    ticket_id = record.ticket_id

    try:
        progress = run_workflow(
            session=session,
            approval_id=approval_id,
            viewer=requester,
            checkpoint_path=checkpoint_path,
            action="start",
            allow_execution=False,
        )

    except (
        service.ApprovalStorageError,
        service.ApprovalConflictError,
        service.ApprovalAccessError,
    ):
        # 申请已保存，不能把启动异常理解成“申请不存在”。
        # 返回原编号，让调用方查询进度并继续处理。
        return RequestTicketClosureResult(
            approval_id=approval_id,
            operation_id=operation_id,
            ticket_id=ticket_id,
            workflow_started=False,
            message=(
                f"关闭申请已保存，审批申请编号：{approval_id}。"
                "尚未确认工作流启动成功，请查询该申请的流程进度后继续处理，"
                "不要重复创建申请。"
            ),
        )

    return RequestTicketClosureResult(
        approval_id=approval_id,
        operation_id=operation_id,
        ticket_id=ticket_id,
        workflow_started=progress.started,
        message=(
            f"关闭申请已提交，审批申请编号：{approval_id}。"
            "工作流已启动，请由主管或管理员审核并继续流程。"
        ),
    )
