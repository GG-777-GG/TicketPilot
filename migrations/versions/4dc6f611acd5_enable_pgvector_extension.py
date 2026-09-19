"""
为 TicketPilot 数据库启用 pgvector 扩展。

文件职责：
- 让 PostgreSQL 获得保存和检索向量的能力。
- 保证这个数据库变化由 Alembic 统一管理。
- 兼容测试使用的 SQLite 数据库。

正常升级流程：
uv run alembic upgrade head
→ Alembic 调用 upgrade()
→ 取得当前数据库连接
→ 如果是 PostgreSQL，执行 CREATE EXTENSION
→ 如果是 SQLite，直接跳过
→ Alembic 记录新的版本号

正常回滚流程：
uv run alembic downgrade -1
→ Alembic 调用 downgrade()
→ 如果是 PostgreSQL，删除 vector 扩展
→ 如果是 SQLite，直接跳过

异常流程：
- PostgreSQL 镜像没有安装 pgvector 时，CREATE EXTENSION 会失败。
- 数据库用户没有足够权限时，迁移会失败。
- 迁移失败时，Alembic 不会把本次版本标记为已完成。

完整调用链：
终端命令
→ migrations/env.py
→ 本文件的 upgrade() 或 downgrade()
→ PostgreSQL
→ vector 扩展
"""

# Sequence 和 Union 用来声明 Alembic 迁移版本字段的类型。
from typing import (
    Sequence,
    Union,
)

# op 提供 Alembic 的数据库迁移操作。
from alembic import op


# 当前迁移文件的唯一版本编号。
revision: str = "4dc6f611acd5"

# 当前迁移的上一个版本。
# Alembic 根据它确定迁移执行顺序。
down_revision: Union[
    str,
    Sequence[str],
    None,
] = "d730dcf4c7e6"

# 当前迁移不属于额外的迁移分支。
branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None

# 当前迁移没有其他额外依赖。
depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


# 把数据库升级到支持 pgvector 的版本。
def upgrade() -> None:
    # 取得 Alembic 当前正在使用的数据库连接。
    # 这里不会创建新的连接，而是复用本次迁移的连接。
    database_connection = op.get_bind()

    # 项目的自动化迁移测试使用 SQLite。
    # SQLite 不支持 PostgreSQL 的 CREATE EXTENSION，
    # 所以遇到 SQLite 或其他数据库时直接结束。
    if (
        database_connection.dialect.name
        != "postgresql"
    ):
        return

    # 在当前 PostgreSQL 数据库中启用 vector 扩展。
    # IF NOT EXISTS 表示扩展已经启用时也不会报错。
    op.execute(
        "CREATE EXTENSION IF NOT EXISTS vector"
    )


# 把数据库回滚到尚未启用 pgvector 的版本。
def downgrade() -> None:
    # 取得当前迁移使用的数据库连接。
    database_connection = op.get_bind()

    # SQLite 没有 vector 扩展，所以不需要执行删除操作。
    if (
        database_connection.dialect.name
        != "postgresql"
    ):
        return

    # 从当前 PostgreSQL 数据库删除 vector 扩展。
    # IF EXISTS 表示扩展不存在时也不会报错。
    op.execute(
        "DROP EXTENSION IF EXISTS vector"
    )