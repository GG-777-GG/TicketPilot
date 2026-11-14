"""
文件职责：创建审批成功执行结果表，保护操作编号的一对一结果记录。
作用与配合：与 ApprovalExecutionRecord 对齐，由 Alembic 升级和回滚。
边界：仅改变表结构，不执行工单关闭或生成业务执行记录。

Revision ID: 91b7e4c2a6d0
Revises: 6cfe2a7d9b41
"""

from alembic import op
import sqlalchemy as sa


revision = "91b7e4c2a6d0"
down_revision = "6cfe2a7d9b41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增成功执行结果表，已有审批申请保持不变。"""

    op.create_table(
        "approval_executions",
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("ticket_id", sa.String(36), nullable=False),
        sa.Column("executed_by", sa.String(36), nullable=False),
        sa.Column("action", sa.String(30), server_default="close_ticket", nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.ForeignKeyConstraint(["operation_id"], ["approval_requests.operation_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["executed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("action = 'close_ticket'", name="ck_approval_executions_action"),
    )


def downgrade() -> None:
    """删除执行结果表及其中数据，保留审批申请和原业务表。"""

    op.drop_table("approval_executions")
