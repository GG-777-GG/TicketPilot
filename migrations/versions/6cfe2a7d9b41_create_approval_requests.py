"""
文件职责：创建正式审批申请表及其数据库约束、索引。
作用与配合：与 approvals.models 对齐，由 Alembic 升级和回滚。
边界：不创建审批申请，不修改旧用户、工单或知识文档。

Revision ID: 6cfe2a7d9b41
Revises: 0ea19e381c10
"""

from alembic import op
import sqlalchemy as sa


revision = "6cfe2a7d9b41"
down_revision = "0ea19e381c10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增审批表，保留原有业务表和记录。"""

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("ticket_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(30), server_default="close_ticket", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("requested_by", sa.String(36), nullable=False),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("request_reason", sa.String(500), nullable=False),
        sa.Column("approval_reason", sa.String(500), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("action = 'close_ticket'", name="ck_approval_requests_action"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executing', 'succeeded', 'failed')",
            name="ck_approval_requests_status",
        ),
    )
    for column in ("ticket_id", "status", "requested_by"):
        op.create_index(f"ix_approval_requests_{column}", "approval_requests", [column])


def downgrade() -> None:
    """删除本次新增的审批表；其中的审批记录也会被删除。"""

    for column in ("requested_by", "status", "ticket_id"):
        op.drop_index(f"ix_approval_requests_{column}", table_name="approval_requests")
    op.drop_table("approval_requests")
