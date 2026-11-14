"""allow_cancelled_ingestion_jobs

Revision ID: 9fd8fbd7254b
Revises: 7e612a61d1a5
Create Date: 2026-10-31 18:17:31.664850

"""
# 文件职责：允许入库任务保存 cancelled 状态。
# 作用与配合：与任务状态枚举和 ORM 检查约束保持一致。
# 使用位置：通过 Alembic 升级数据库结构。
# 边界：不取消具体任务，不删除或改写现有任务数据。
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9fd8fbd7254b'
down_revision: Union[str, Sequence[str], None] = '7e612a61d1a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """允许保存 cancelled 状态，保留现有任务。"""

    with op.batch_alter_table(
        "knowledge_ingestion_jobs",
    ) as batch_op:
        # 先移除原来只允许四种状态的约束。
        batch_op.drop_constraint(
            "ck_knowledge_ingestion_jobs_status",
            type_="check",
        )

        # 使用相同名称，建立允许已取消状态的新约束。
        batch_op.create_check_constraint(
            "ck_knowledge_ingestion_jobs_status",
            (
                "status IN "
                "('queued', 'running', 'succeeded', "
                "'failed', 'cancelled')"
            ),
        )


def downgrade() -> None:
    """确认旧版能表示现有任务后，再恢复旧约束。"""

    connection = op.get_bind()

    # 只查询，不删除任务，也不把已取消任务改成失败。
    cancelled_id = connection.execute(
        sa.text(
            "SELECT id FROM knowledge_ingestion_jobs "
            "WHERE status = 'cancelled' LIMIT 1"
        )
    ).scalar_one_or_none()

    # 旧约束无法保存 cancelled，因此存在这类数据时拒绝回退。
    if cancelled_id is not None:
        raise RuntimeError(
            "存在已取消任务，无法直接恢复旧状态约束；"
            "请先制定兼容方案"
        )

    with op.batch_alter_table(
        "knowledge_ingestion_jobs",
    ) as batch_op:
        batch_op.drop_constraint(
            "ck_knowledge_ingestion_jobs_status",
            type_="check",
        )

        batch_op.create_check_constraint(
            "ck_knowledge_ingestion_jobs_status",
            (
                "status IN "
                "('queued', 'running', 'succeeded', 'failed')"
            ),
        )