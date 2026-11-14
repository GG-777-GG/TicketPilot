"""
文件职责：定义审批申请和成功执行结果的数据库表。
作用与配合：由审批 Repository 和 Service 保存、查询记录，
由 Alembic 迁移创建实际数据库表。
边界：模型本身不处理权限、不执行审批或关闭工单。
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ticketpilot.core.database import Base


class ApprovalRecord(Base):
    """数据库中一条申请关闭工单的审批记录。"""

    __tablename__ = "approval_requests"

    # 数据库约束：即使绕过接口直接写入，也限制合法取值。
    __table_args__ = (
        CheckConstraint(
            "action = 'close_ticket'",
            name="ck_approval_requests_action",
        ),
        CheckConstraint(
            "status IN ("
            "'pending', 'approved', 'rejected', "
            "'executing', 'succeeded', 'failed'"
            ")",
            name="ck_approval_requests_status",
        ),
    )

    # 审批记录编号，由后端创建申请时生成。
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    # 本次业务操作的稳定编号，不允许两条审批记录重复使用。
    operation_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        unique=True,
    )

    # 对应真实工单；存在审批记录时限制删除关联工单。
    ticket_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tickets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    action: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="close_ticket",
        server_default="close_ticket",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )

    # 申请人必须对应 users 表中存在的用户。
    requested_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # 尚未审批时没有审批人。
    reviewed_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    request_reason: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    approval_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # 执行结果可能比简短理由更长，使用 Text。
    result: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

class ApprovalExecutionRecord(Base):
    """保存一次已成功执行的业务操作及其结果。"""

    __tablename__ = "approval_executions"

    __table_args__ = (
        CheckConstraint(
            "action = 'close_ticket'",
            name="ck_approval_executions_action",
        ),
    )

    # 同一个操作只允许保存一条成功结果。
    # 同时要求它对应一条实际存在的审批申请。
    operation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "approval_requests.operation_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )

    # 本次操作实际作用于哪张工单。
    ticket_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "tickets.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    # 后端记录实际发起执行的登录用户。
    executed_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="close_ticket",
        server_default="close_ticket",
    )

    # 成功执行时保存的结果，重复调用时可以直接返回。
    result: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )