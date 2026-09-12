"""add ticket timestamps

Revision ID: 1546b4e6b921
Revises: 9782abec9f36
Create Date: 2026-09-12 17:05:07.277160

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1546b4e6b921'
down_revision: Union[str, Sequence[str], None] = '9782abec9f36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 将数据库结构升级到包含工单时间字段的新版本
def upgrade() -> None:
    # 第一阶段：先添加允许 NULL 的 created_at
    # 旧工单暂时没有创建时间，所以不能直接设置 NOT NULL
    op.add_column(
        # 指定需要修改的数据库表
        "tickets",

        # 定义需要添加的新列
        sa.Column(
            # 数据库字段名称
            "created_at",

            # 使用带时区的日期时间类型
            sa.DateTime(timezone=True),

            # 暂时允许为空，保证旧数据可以完成升级
            nullable=True,
        ),
    )

    # 第一阶段：添加允许 NULL 的 updated_at
    op.add_column(
        # 指定需要修改的数据库表
        "tickets",

        # 定义需要添加的新列
        sa.Column(
            # 数据库字段名称
            "updated_at",

            # 使用带时区的日期时间类型
            sa.DateTime(timezone=True),

            # 暂时允许为空
            nullable=True,
        ),
    )

    # 第二阶段：给已有工单填写创建时间
    op.execute(
        # 把字符串包装成 SQLAlchemy SQL 语句
        sa.text(
            """
            UPDATE tickets
            SET created_at = CURRENT_TIMESTAMP
            WHERE created_at IS NULL
            """
        )
    )

    # 第二阶段：让旧工单的初始更新时间等于创建时间
    op.execute(
        # 执行数据回填 SQL
        sa.text(
            """
            UPDATE tickets
            SET updated_at = created_at
            WHERE updated_at IS NULL
            """
        )
    )

    # 第三阶段：把两个字段改成正式的非空字段
    # batch 模式兼容 PostgreSQL 和测试使用的 SQLite
    with op.batch_alter_table(
        "tickets",
    ) as batch_op:
        # 完成 created_at 的正式约束
        batch_op.alter_column(
            # 指定需要修改的字段
            "created_at",

            # 告诉 Alembic 当前字段类型
            existing_type=sa.DateTime(
                timezone=True,
            ),

            # 回填完成后，不再允许保存 NULL
            nullable=False,

            # 以后新增工单时由数据库生成默认时间
            server_default=sa.text(
                "CURRENT_TIMESTAMP"
            ),
        )

        # 完成 updated_at 的正式约束
        batch_op.alter_column(
            # 指定需要修改的字段
            "updated_at",

            # 告诉 Alembic当前字段类型
            existing_type=sa.DateTime(
                timezone=True,
            ),

            # 回填完成后，不再允许保存 NULL
            nullable=False,

            # 新建工单时生成初始更新时间
            server_default=sa.text(
                "CURRENT_TIMESTAMP"
            ),
        )


# 将数据库结构退回没有时间字段的旧版本
def downgrade() -> None:
    # batch 模式使 SQLite 也能通过重建表来删除字段
    with op.batch_alter_table(
        "tickets",
    ) as batch_op:
        # 先删除 updated_at
        batch_op.drop_column(
            "updated_at"
        )

        # 再删除 created_at
        batch_op.drop_column(
            "created_at"
        )