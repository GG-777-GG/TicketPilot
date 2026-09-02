from uuid import uuid4

from fastapi import (
    FastAPI,
    HTTPException,
    status
)

from ticketpilot.models import Ticket
from ticketpilot.schemas import (
    TicketResponse,
    TicketCreateRequest,
    TicketStatusUpdateRequest,
)
from ticketpilot.storage import (
    load_tickets,
    save_tickets,
)

app = FastAPI(
    title="TicketPilot API",
    description="企业智能工单与知识库 Agent API",
    version="0.2.0",
)


def to_ticket_response(
        ticket: Ticket,
) -> TicketResponse:
    return TicketResponse(
        id=ticket.id,
        customer_name=ticket.customer_name,
        contact_email=ticket.contact_email,
        channel=ticket.channel,
        subject=ticket.subject,
        description=ticket.description,
        priority=ticket.priority,
        status=ticket.status,
    )


# 从存储层安全读取全部工单
def load_tickets_or_500() -> list[Ticket]:
    try:
        return load_tickets()
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工单服务暂时不可用"
        )from error


# 返回服务当前的基础健康状态
@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "TicketPilot"
    }


# 查询全部工单
@app.get(
    "/v1/tickets",
    response_model=list[TicketResponse],
    tags=["tickets"],
)
def list_tickets() -> list[TicketResponse]:
    tickets = load_tickets_or_500()

    return [
        to_ticket_response(ticket)
        for ticket in tickets
    ]


# 根据工单 ID 查询单张工单
@app.get(
    "/v1/tickets/{ticket_id}",
    response_model=TicketResponse,
    tags=["tickets"],
)
def get_ticket(ticket_id: str) -> TicketResponse:
    tickets = load_tickets_or_500()

    for ticket in tickets:
        if ticket.id == ticket_id:
            return to_ticket_response(ticket)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="工单不存在",
    )


# 根据工单 ID 修改状态
@app.patch(
    "/v1/tickets/{ticket_id}/status",
    response_model=TicketResponse,
    tags=["tickets"],
)
def update_ticket_status(
        ticket_id: str,
        request: TicketStatusUpdateRequest,
) -> TicketResponse:
    tickets = load_tickets_or_500()

    for ticket in tickets:
        if ticket.id == ticket_id:
            old_status = ticket.status
            ticket.status = request.status

            try:
                save_tickets(tickets)

            except RuntimeError as error:
                ticket.status = old_status

                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="工单服务暂时不可用",
                ) from error

            return to_ticket_response(ticket)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="工单不存在",
    )


# 根据工单 ID 删除工单
@app.delete(
    "/v1/tickets/{ticket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["tickets"],
)
def delete_ticket(ticket_id: str) -> None:
    tickets = load_tickets_or_500()

    for index, ticket in enumerate(tickets):
        if ticket.id == ticket_id:
            deleted_ticket = tickets.pop(index)

            try:
                save_tickets(tickets)

            except RuntimeError as error:
                tickets.insert(
                    index,
                    deleted_ticket,
                )

                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="工单服务暂时不可用",
                ) from error

            return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="工单不存在",
    )


# 验证请求并创建一张新工单
@app.post(
    "/v1/tickets",
    response_model=TicketResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["tickets"],
)
def create_ticket(
        request: TicketCreateRequest,
) -> TicketResponse:
    ticket = Ticket(
        id=str(uuid4()),
        customer_name=request.customer_name,
        contact_email=str(request.contact_email),
        channel=request.channel,
        subject=request.subject,
        description=request.description,
        priority=request.priority,
    )

    tickets = load_tickets_or_500()
    tickets.append(ticket)

    try:
        save_tickets(tickets)

    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="工单服务暂时不可用",
        ) from error

    return to_ticket_response(ticket)
