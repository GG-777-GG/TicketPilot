from dataclasses import dataclass


PROJECT_NAME = "TicketPilot"

ALLOWED_CHANNELS = {"web", "phone", "email", "chat"}
ALLOWED_PRIORITIES = {"low", "medium", "high"}
ALLOWED_STATUSES = {
    "open",
    "in_progress",
    "resolved",
    "closed",
}


# 表示一张结构明确、可以修改状态的客户工单
@dataclass
class Ticket:
    id: str
    customer_name: str
    contact_email: str
    channel: str
    subject: str
    description: str
    priority: str
    status: str = "open"