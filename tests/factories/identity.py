"""创建身份模块测试对象的工厂函数。"""

from uuid import uuid4

from ticketpilot.identity.models import UserRecord


def build_user(
    *,
    user_id: str | None = None,
    email: str | None = None,
    display_name: str = "测试管理员",
    password_hash: str = "test-password-hash",
    role: str = "admin",
    is_active: bool = True,
) -> UserRecord:
    """构造用户 ORM 对象，但不执行 add、flush 或 commit。"""

    return UserRecord(
        id=(
            user_id
            if user_id is not None
            else str(uuid4())
        ),
        email=(
            email
            if email is not None
            else f"{uuid4()}@example.com"
        ),
        display_name=display_name,
        password_hash=password_hash,
        role=role,
        is_active=is_active,
    )
