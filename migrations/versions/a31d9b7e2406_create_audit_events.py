"""
文件职责：新增审计事件快照表，与AuditEventRecord对应。
作用与配合：由Alembic升级/回滚，不写入或修改实际业务事件。
边界：本迁移不提供防篡改能力，不删除用户、工单或审批记录。

Revision ID: a31d9b7e2406
Revises: 91b7e4c2a6d0
"""

from alembic import op
import sqlalchemy as sa


revision = "a31d9b7e2406"
down_revision = "91b7e4c2a6d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增不依赖业务对象生命周期的事件表及常用查询索引。"""
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("approval_id", sa.String(36), nullable=True),
        sa.Column("ticket_id", sa.String(36), nullable=True),
        sa.Column("operation_id", sa.String(36), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed', 'replayed')",
            name="ck_audit_events_outcome",
        ),
    )
    for column in ("actor_id", "operation_id", "occurred_at"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])


def downgrade() -> None:
    """只删除审计表和其中事件，不改变原业务表。"""
    for column in ("occurred_at", "operation_id", "actor_id"):
        op.drop_index(f"ix_audit_events_{column}", table_name="audit_events")
    op.drop_table("audit_events")
