"""add knowledge visibility

Revision ID: 3544e7a0e157
Revises: baf42e466d80
Create Date: 2026-10-11 01:06:10.205571

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3544e7a0e157'
down_revision: Union[str, Sequence[str], None] = 'baf42e466d80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增可见性字段，旧文档自动设为 internal。"""
    # batch 模式同时兼容 PostgreSQL 和测试使用的 SQLite。
    with op.batch_alter_table("knowledge_documents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "visibility",
                sa.String(length=20),
                nullable=False,
                server_default="internal",
            )
        )
        # 即使绕过 Python 直接写 SQL，也不允许未知权限值。
        batch_op.create_check_constraint(
            "ck_knowledge_documents_visibility",
            "visibility IN ('internal', 'restricted')",
        )


def downgrade() -> None:
    """回滚可见性字段，不删除知识文档。"""
    with op.batch_alter_table("knowledge_documents") as batch_op:
        batch_op.drop_constraint(
            "ck_knowledge_documents_visibility",
            type_="check",
        )
        batch_op.drop_column("visibility")