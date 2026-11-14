"""add_cancelling_ingestion_job_status

Revision ID: 0ea19e381c10
Revises: 9fd8fbd7254b
Create Date: 2026-10-31 18:40:54.454653

"""

# 文件职责：允许入库任务保存 cancelling 中间状态。
# 作用与配合：与任务枚举、ORM 状态约束保持一致。
# 使用位置：通过 Alembic 执行数据库结构升级。
# 边界：不申请取消具体任务，也不修改已有任务状态。
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0ea19e381c10'
down_revision: Union[str, Sequence[str], None] = '9fd8fbd7254b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """增加 cancelling 状态，保留现有任务。"""

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
                "('queued', 'running', 'cancelling', "
                "'succeeded', 'failed', 'cancelled')"
            ),
        )


def downgrade() -> None:
    """没有正在取消的任务时，才允许恢复旧约束。"""

    connection = op.get_bind()

    # 旧约束不能表示 cancelling，先检查是否存在这种任务。
    pending_id = connection.execute(
        sa.text(
            "SELECT id FROM knowledge_ingestion_jobs "
            "WHERE status = 'cancelling' LIMIT 1"
        )
    ).scalar_one_or_none()

    if pending_id is not None:
        raise RuntimeError(
            "存在正在取消的任务，不能恢复旧状态约束；"
            "请先完成取消收尾"
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
                "('queued', 'running', 'succeeded', "
                "'failed', 'cancelled')"
            ),
        )