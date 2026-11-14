"""add ingestion job file fields

Revision ID: 9606ce5dd746
Revises: 2d9a3f0abac4
Create Date: 2026-10-23 11:05:42.646337

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9606ce5dd746'
down_revision: Union[str, Sequence[str], None] = '2d9a3f0abac4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """增加文件任务字段，并限制任务输入组合。"""

    # 把同一张表的修改放在一个批量操作中。
    # SQLite 必要时通过重建表并复制数据完成修改；
    # PostgreSQL 默认使用它支持的 ALTER TABLE 操作。
    with op.batch_alter_table(
        "knowledge_ingestion_jobs",
    ) as batch_op:
        # 文本任务不需要文件标识，因此允许为空。
        batch_op.add_column(
            sa.Column(
                "storage_key",
                sa.String(length=36),
                nullable=True,
            )
        )

        # 保存原始文件名，供 Worker 选择解析器。
        batch_op.add_column(
            sa.Column(
                "original_filename",
                sa.String(length=255),
                nullable=True,
            )
        )

        # 文件任务解析前没有正文，因此解除非空限制。
        batch_op.alter_column(
            "raw_content",
            existing_type=sa.Text(),
            nullable=True,
        )

        # 保证每条任务只有一种完整输入：
        # 已有正文，或等待解析的文件。
        batch_op.create_check_constraint(
            # 与 ORM 中的约束名称一致。
            constraint_name="ck_knowledge_ingestion_jobs_input",

            # 当前操作已经绑定任务表，不必重复传 table_name。
            condition=(
                "("
                "raw_content IS NOT NULL "
                "AND storage_key IS NULL "
                "AND original_filename IS NULL"
                ") OR ("
                "raw_content IS NULL "
                "AND storage_key IS NOT NULL "
                "AND original_filename IS NOT NULL "
                "AND source_type = 'file'"
                ")"
            ),
        )

def downgrade() -> None:
    """确认旧结构能保存现有任务后，再执行回滚。"""

    # 获取 Alembic 当前迁移使用的数据库连接。
    connection = op.get_bind()

    # 检查是否有旧版表结构无法完整保存的任务。
    # 只找一条即可，不需要读取全部任务。
    incompatible_job_id = connection.execute(
        sa.text(
            """
            SELECT id
            FROM knowledge_ingestion_jobs
            WHERE raw_content IS NULL
               OR storage_key IS NOT NULL
               OR original_filename IS NOT NULL
            LIMIT 1
            """
        )
    ).scalar_one_or_none()

    # 存在文件输入等旧结构不支持的数据时，停止回滚。
    # 不删除任务、不伪造正文，也不继续删除字段。
    if incompatible_job_id is not None:
        raise RuntimeError(
            "无法回滚：存在旧版结构无法保存的文件任务，"
            "请先制定数据转换或归档方案"
        )

    # 第一步：先移除引用新字段的检查约束。
    # 只有前面的数据检查通过，才进入表结构修改。
    # 使用批量模式，兼容测试使用的 SQLite。
    with op.batch_alter_table(
            "knowledge_ingestion_jobs",
    ) as batch_op:
        # 第一步：移除引用新字段的约束。
        # 批量操作已经指定表名，这里不用再传 table_name。
        batch_op.drop_constraint(
            constraint_name="ck_knowledge_ingestion_jobs_input",
            type_="check",
        )

        # 第二步：恢复旧版的正文非空要求。
        # 前面的查询已确认没有 raw_content 为 NULL 的任务。
        batch_op.alter_column(
            "raw_content",
            existing_type=sa.Text(),
            nullable=False,
        )

        # 第三步：移除旧版没有的字段。
        # 保护检查已确认这两列没有需要保留的文件信息。
        batch_op.drop_column(
            "original_filename",
        )
        batch_op.drop_column(
            "storage_key",
        )