"""工单领域中可复用的基础类型。"""

from typing import Literal

Channel = Literal["web", "phone", "email", "chat"]
Priority = Literal["low", "medium", "high"]
TicketStatus = Literal[
    "open",
    "in_progress",
    "resolved",
    "closed",
]
