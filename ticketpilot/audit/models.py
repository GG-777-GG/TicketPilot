"""
文件职责：定义数据库审计事件表。
作用与配合：由Repository添加，业务Service管理事务。
边界：保存历史快照，不修改业务状态，不负责权限判断。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ticketpilot.core.database import Base


class AuditEventRecord(Base):
    """数据库中一条独立的审计事件。"""

    __tablename__ = "audit_events"

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed', 'replayed')",
            name="ck_audit_events_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    # 历史身份和目标不设业务外键，保留发生事件时的标识。
    actor_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )

    actor_role: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    approval_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )

    ticket_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )

    operation_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )

    outcome: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )

    request_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )

    trace_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )