"""add_ingestion_job_processing_stage

Revision ID: 7e612a61d1a5
Revises: 9606ce5dd746
Create Date: 2026-10-30 11:37:39.652055

"""

# 文件职责：给入库任务增加处理阶段字段。
# 作用与配合：供 Worker 保存阶段，供任务查询接口读取。
# 使用位置：通过 Alembic upgrade 执行数据库结构升级。
# 边界：只改变存储结构，不负责实际处理任务。

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e612a61d1a5'
down_revision: Union[str, Sequence[str], None] = '9606ce5dd746'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """增加任务阶段字段，旧任务暂时保持为空。"""

    # 同一张表的修改放在一个批量操作中。
    with op.batch_alter_table(
        "knowledge_ingestion_jobs",
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                # 当前任务执行到的阶段。
                "processing_stage",

                # 保存 parsing、embedding 等字符串。
                sa.String(length=20),

                # 未开始处理，以及旧任务，允许为空。
                nullable=True,
            )
        )

        # 数据库也限制阶段的合法值。
        batch_op.create_check_constraint(
            "ck_knowledge_ingestion_jobs_processing_stage",
            (
                "processing_stage IS NULL "
                "OR processing_stage IN "
                "('parsing', 'cleaning', 'chunking', "
                "'embedding', 'indexing')"
            ),
        )


def downgrade() -> None:
    """移除阶段字段，保留任务原有输入和状态。"""

    with op.batch_alter_table(
        "knowledge_ingestion_jobs",
    ) as batch_op:
        # 先删除引用这个字段的约束。
        batch_op.drop_constraint(
            "ck_knowledge_ingestion_jobs_processing_stage",
            type_="check",
        )

        # 再删除字段。
        batch_op.drop_column("processing_stage")
