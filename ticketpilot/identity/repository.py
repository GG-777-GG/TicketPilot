
# 导入 select，用于构造用户查询 SQL
from sqlalchemy import select

# 导入数据库完整性异常和通用异常
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)

# 导入 SQLAlchemy Session 类型
from sqlalchemy.orm import Session

# 导入用户 ORM 模型
from ticketpilot.identity.models import UserRecord


# 表示创建用户时邮箱已经存在
class UserAlreadyExistsError(RuntimeError):
    # 目前不需要额外代码
    pass


# 创建并保存一名新用户
def create_user(
    # 接收当前数据库 Session
    session: Session,

    # 接收需要保存的用户 ORM 对象
    user: UserRecord,
) -> UserRecord:
    # 捕获数据库写入异常
    try:
        # 统一删除邮箱两端空格并转换成小写
        # 保证 GG@EXAMPLE.COM 和 gg@example.com 被视为同一邮箱
        user.email = (
            user.email
            .strip()
            .lower()
        )

        # 将用户对象加入当前 Session
        session.add(
            user
        )

        # 提交事务，执行 INSERT
        session.commit()

        # 返回已经保存的用户对象
        return user

    # IntegrityError 包括唯一约束冲突
    except IntegrityError as error:
        # 让 Session 离开失败事务状态
        session.rollback()

        # 转换成 API 能识别的重复用户异常
        raise UserAlreadyExistsError(
            "用户邮箱已存在"
        ) from error

    # 捕获其他 SQLAlchemy 数据库异常
    except SQLAlchemyError as error:
        # 撤销尚未成功提交的数据
        session.rollback()

        # 转换成项目统一的运行时异常
        raise RuntimeError(
            "创建用户失败"
        ) from error


# 根据邮箱查询一名用户
def get_user_by_email(
    # 接收当前数据库 Session
    session: Session,

    # 接收需要查询的邮箱
    email: str,
) -> UserRecord | None:
    # 捕获数据库查询异常
    try:
        # 使用与创建用户时相同的邮箱规范化规则
        normalized_email = (
            email
            .strip()
            .lower()
        )

        # 构造按邮箱查询用户的 SQL
        statement = (
            select(UserRecord)
            .where(
                UserRecord.email
                == normalized_email
            )
        )

        # 找到时返回 UserRecord
        # 找不到时返回 None
        return session.scalar(
            statement
        )

    # 捕获 SQLAlchemy 查询异常
    except SQLAlchemyError as error:
        # 清理失败的数据库事务
        session.rollback()

        # 不向上层泄漏数据库内部错误
        raise RuntimeError(
            "查询用户失败"
        ) from error


# 根据用户 ID 查询一名用户
def get_user_by_id(
    # 接收当前数据库 Session
    session: Session,

    # 接收目标用户 ID
    user_id: str,
) -> UserRecord | None:
    # 捕获数据库查询异常
    try:
        # Session.get() 专门用于根据主键查询
        return session.get(
            # 指定 users 表对应的 ORM 模型
            UserRecord,

            # 指定需要查询的主键值
            user_id,
        )

    # 捕获 SQLAlchemy 查询异常
    except SQLAlchemyError as error:
        # 清理失败的事务状态
        session.rollback()

        # 转换成稳定的项目异常
        raise RuntimeError(
            "查询用户失败"
        ) from error
