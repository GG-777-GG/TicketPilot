from sqlalchemy import (
    String,
    Text
)

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from ticketpilot.database import Base

# 表示数据库 tickets 表中的一条工单记录
class TicketRecord(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True
    )

    customer_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    contact_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    priority: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # 保存当前负责处理工单的人员姓名
    assigned_to: Mapped[str | None] = mapped_column(
        # 数据库中最多保存 100 个字符
        String(100),
        # 允许数据库保存 NULL，表示工单尚未分配
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="open",
    )