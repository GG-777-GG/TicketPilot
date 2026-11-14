"""add chunk page numbers

Revision ID: 2d9a3f0abac4
Revises: 263e791b7093
Create Date: 2026-10-17 20:12:00.057114

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d9a3f0abac4'
down_revision: Union[str, Sequence[str], None] = '263e791b7093'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """升级：为知识片段增加来源页码。"""

    op.add_column(
        "knowledge_chunks",  # 要修改的数据库表。
        sa.Column(
            "page_numbers",  # 新字段名。
            sa.JSON(),       # 保存 [1] 或 [1, 2] 这样的页码列表。
            nullable=False,  # 每条片段记录都必须有这个字段。
            server_default="[]",  # 旧片段没有页码，数据库填空列表。
        ),
    )


def downgrade() -> None:
    """回退迁移：删除新增的页码字段。"""

    op.drop_column(
        "knowledge_chunks",  # 从哪张表删除。
        "page_numbers",       # 删除哪个字段。
    )
