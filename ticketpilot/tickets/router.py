"""工单模块的 HTTP 路由。"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status

from ticketpilot.core.dependencies import DatabaseSession
from ticketpilot.identity.dependencies import (
    CurrentUser,
    SupervisorOrAdminUser,
)
from ticketpilot.tickets import repository as ticket_repository
from ticketpilot.tickets.models import TicketRecord
from ticketpilot.tickets.schemas import (
    TicketAssigneeUpdateRequest,
    TicketCreateRequest,
    TicketResponse,
    TicketStatusUpdateRequest,
)


router = APIRouter(
    prefix="/v1/tickets",
    tags=["tickets"],
)


def to_ticket_response(ticket: TicketRecord) -> TicketResponse:
    """把数据库 ORM 工单转换成公开的 API 响应。"""

    return TicketResponse(
        id=ticket.id,
        customer_name=ticket.customer_name,
        contact_email=ticket.contact_email,
        channel=ticket.channel,
        subject=ticket.subject,
        description=ticket.description,
        priority=ticket.priority,
        status=ticket.status,
        assigned_to=ticket.assigned_to,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


def _get_ticket_or_404(
    session: DatabaseSession,
    ticket_id: str,
) -> TicketRecord:
    """统一查询一张工单，并把数据库/不存在错误转换成 HTTP 错误。"""

    try:
        ticket = ticket_repository.get_ticket_by_id(
            session,
            ticket_id,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工单服务暂时不可用",
        ) from error

    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="工单不存在",
        )

    return ticket


@router.get(
    "",
    response_model=list[TicketResponse],
)
def list_tickets(
    session: DatabaseSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TicketResponse]:
    """按创建时间倒序分页查询工单。"""

    try:
        tickets = ticket_repository.list_tickets(
            session,
            limit=limit,
            offset=offset,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工单服务暂时不可用",
        ) from error

    return [to_ticket_response(ticket) for ticket in tickets]


@router.get(
    "/{ticket_id}",
    response_model=TicketResponse,
)
def get_ticket(
    ticket_id: str,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> TicketResponse:
    """根据 ID 查询一张工单。"""

    return to_ticket_response(
        _get_ticket_or_404(session, ticket_id),
    )


@router.patch(
    "/{ticket_id}/status",
    response_model=TicketResponse,
)
def update_ticket_status(
    ticket_id: str,
    request: TicketStatusUpdateRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> TicketResponse:
    """修改普通工单状态；关闭操作必须通过审批执行。"""

    # 先确认工单存在；不存在时仍然返回 404。
    ticket = _get_ticket_or_404(session, ticket_id)

    # 在写数据库之前拦截直接关闭，所有角色都遵守这条规则。
    if request.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="关闭工单需要提交审批申请，并在获批后执行",
        )

    try:
        # 其他合法状态仍按原来的流程保存。
        updated_ticket = ticket_repository.update_ticket_status(
            session,
            ticket,
            request.status,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工单服务暂时不可用",
        ) from error

    return to_ticket_response(updated_ticket)


@router.patch(
    "/{ticket_id}/assignee",
    response_model=TicketResponse,
)
def update_ticket_assignee(
    ticket_id: str,
    request: TicketAssigneeUpdateRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> TicketResponse:
    """分配处理人；传入 null 时取消分配。"""

    ticket = _get_ticket_or_404(session, ticket_id)
    try:
        updated_ticket = ticket_repository.update_ticket_assignee(
            session,
            ticket,
            request.assigned_to,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工单服务暂时不可用",
        ) from error

    return to_ticket_response(updated_ticket)


@router.delete(
    "/{ticket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_ticket(
    ticket_id: str,
    session: DatabaseSession,
    current_user: SupervisorOrAdminUser,
) -> None:
    """删除工单；只有主管或管理员可以执行。"""

    ticket = _get_ticket_or_404(session, ticket_id)
    try:
        ticket_repository.delete_ticket(session, ticket)
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工单服务暂时不可用",
        ) from error


@router.post(
    "",
    response_model=TicketResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_ticket(
    request: TicketCreateRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> TicketResponse:
    """验证请求并创建一张新工单。"""

    ticket = TicketRecord(
        id=str(uuid4()),
        customer_name=request.customer_name,
        contact_email=str(request.contact_email),
        channel=request.channel,
        subject=request.subject,
        description=request.description,
        priority=request.priority,
        status="open",
    )

    try:
        created_ticket = ticket_repository.create_ticket(
            session,
            ticket,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工单服务暂时不可用",
        ) from error

    return to_ticket_response(created_ticket)
