from datetime import datetime

# 导入数据库字段类型和 SQL 函数
from sqlalchemy import (
    # 表示数据库中的布尔类型
    Boolean,

    # 表示日期时间类型
    DateTime,

    # 表示有长度限制的字符串
    String,

    # 表示长文本
    Text,

    # 提供 now() 等 SQL 函数
    func,

    # 生成跨 PostgreSQL 和 SQLite 的 true 默认值
    true,
)

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from ticketpilot.database import Base

# 表示数据库 tickets 表中的一条工单记录
class TicketRecord(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True
    )

    customer_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    contact_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    priority: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # 保存当前负责处理工单的人员姓名
    assigned_to: Mapped[str | None] = mapped_column(
        # 数据库中最多保存 100 个字符
        String(100),
        # 允许数据库保存 NULL，表示工单尚未分配
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="open",
    )

    created_at: Mapped[datetime] = mapped_column(
        # PostgreSQL 中使用带时区的时间类型
        DateTime(timezone=True),

        # 每张工单都必须有创建时间
        nullable=False,

        # 插入数据时由数据库自动生成当前时间
        server_default=func.now(),
    )

    # 保存工单最后一次被修改的时间
    updated_at: Mapped[datetime] = mapped_column(
        # PostgreSQL 中使用带时区的时间类型
        DateTime(timezone=True),

        # 每张工单都必须有更新时间
        nullable=False,

        # 新建工单时由数据库生成初始更新时间
        server_default=func.now(),

        # SQLAlchemy 更新工单时自动写入新的当前时间
        onupdate=func.now(),
    )

# 表示数据库 users 表中的一名系统用户
class UserRecord(Base):
    # 指定对应的数据库表名
    # 使用 users，避免与某些数据库的 user 关键字冲突
    __tablename__ = "users"

    # 用户唯一编号
    id: Mapped[str] = mapped_column(
        # 使用和工单 ID 相同长度的 UUID 字符串
        String(36),

        # 将 id 设置为主键
        primary_key=True,
    )

    # 用户登录邮箱
    email: Mapped[str] = mapped_column(
        # EmailStr 最大长度通常使用 320
        String(320),

        # 每名用户都必须有邮箱
        nullable=False,

        # 不允许两个用户使用同一个规范化邮箱
        unique=True,
    )

    # 用户显示名称
    display_name: Mapped[str] = mapped_column(
        # 最多保存 100 个字符
        String(100),

        # 每名用户都必须有显示名称
        nullable=False,
    )

    # 保存 Argon2 生成的密码哈希
    password_hash: Mapped[str] = mapped_column(
        # 给哈希算法和参数预留足够空间
        String(255),

        # 每名用户都必须有密码哈希
        nullable=False,
    )

    # 保存用户角色，后续用于 RBAC 权限判断
    role: Mapped[str] = mapped_column(
        # 角色名称最多保存 20 个字符
        String(20),

        # 每名用户都必须有角色
        nullable=False,

        # 通过 SQLAlchemy 创建用户时默认是 agent
        default="agent",

        # 通过原生 SQL 创建用户时也默认是 agent
        server_default="agent",
    )

    # 标记账号当前是否允许登录
    is_active: Mapped[bool] = mapped_column(
        # 使用数据库布尔字段
        Boolean,

        # 每名用户都必须有启用状态
        nullable=False,

        # 通过 SQLAlchemy 创建时默认为启用
        default=True,

        # 通过原生 SQL 创建时也默认为启用
        server_default=true(),
    )

    # 保存账号的创建时间
    created_at: Mapped[datetime] = mapped_column(
        # PostgreSQL 使用带时区的时间类型
        DateTime(timezone=True),

        # 每名用户都必须有创建时间
        nullable=False,

        # 插入用户时由数据库自动生成当前时间
        server_default=func.now(),
    )