# 导入 pytest，用于验证预期异常
import pytest
# 导入测试数据库构造工具
from ticketpilot.core.database import (
    build_engine,
    build_session_factory,
    create_tables,
)

# 导入用户 ORM 模型
from ticketpilot.identity.models import UserRecord

# 导入需要测试的用户 Repository 函数
from ticketpilot.identity.repository import (
    UserAlreadyExistsError,
    create_user,
    get_user_by_email,
    get_user_by_id,
)


# 创建一名供 Repository 测试使用的用户
def make_user_record(
    # 允许测试指定用户 ID
    user_id: str = "U-001",

    # 允许测试指定登录邮箱
    email: str = "Agent@Example.COM",
) -> UserRecord:
    # 返回一名尚未保存的 ORM 用户
    return UserRecord(
        # 设置用户 ID
        id=user_id,

        # 故意使用带大写字母的邮箱
        email=email,

        # 设置用户显示名称
        display_name="测试客服",

        # Repository 只负责保存哈希
        password_hash="argon2-hash-for-repository-test",
    )


# 验证用户 Repository 可以创建并查询用户
def test_user_repository_creates_and_reads_user() -> None:
    # 创建本测试独享的内存 SQLite 数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 根据 ORM 模型创建所有数据库表
    create_tables(
        test_engine
    )

    # 创建绑定测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine
    )

    # 使用第一个 Session 创建并查询用户
    with test_session_factory() as session:
        # 创建一名尚未保存的用户
        user = make_user_record()

        # 通过 Repository 保存用户
        created_user = create_user(
            session,
            user,
        )

        # 验证函数返回同一名用户
        assert created_user.id == "U-001"

        # 邮箱应该已经删除空格并转换成小写
        assert (
            created_user.email
            == "agent@example.com"
        )

        # 没有指定角色时默认是 agent
        assert created_user.role == "agent"

        # 新用户默认处于启用状态
        assert created_user.is_active is True

        # 数据库应该自动生成创建时间
        assert created_user.created_at is not None

        # 使用不同大小写和两端空格查询邮箱
        found_by_email = get_user_by_email(
            session,
            "  AGENT@EXAMPLE.COM  ",
        )

        # 应该仍然找到同一名用户
        assert found_by_email is not None
        assert found_by_email.id == "U-001"

        # 根据主键查询用户
        found_by_id = get_user_by_id(
            session,
            "U-001",
        )

        # 应该找到目标用户
        assert found_by_id is not None
        assert (
            found_by_id.email
            == "agent@example.com"
        )

        # 查询不存在的用户
        missing_user = get_user_by_id(
            session,
            "U-999",
        )

        # 找不到时应该返回 None
        assert missing_user is None

    # 使用新 Session 再次查询
    # 证明用户确实已经持久化
    with test_session_factory() as session:
        # 根据邮箱重新查询
        persisted_user = get_user_by_email(
            session,
            "agent@example.com",
        )

        # 用户必须仍然存在
        assert persisted_user is not None

        # 验证保存的是预期用户
        assert persisted_user.id == "U-001"

    # 释放测试数据库连接
    test_engine.dispose()

# 验证重复邮箱会失败并且事务会回滚
def test_create_user_rejects_duplicate_email_and_rolls_back() -> None:
    # 创建本测试独享的内存 SQLite 数据库
    test_engine = build_engine(
        "sqlite:///:memory:",
    )

    # 创建包括 users 在内的全部数据库表
    create_tables(
        test_engine
    )

    # 创建测试数据库的 Session 工厂
    test_session_factory = build_session_factory(
        test_engine
    )

    # 使用一个 Session 完成创建和冲突测试
    with test_session_factory() as session:
        # 创建第一名用户
        first_user = make_user_record(
            user_id="U-001",
            email="agent@example.com",
        )

        # 正常保存第一名用户
        create_user(
            session,
            first_user,
        )

        # 创建第二名用户
        # 邮箱只是在大小写上与第一名不同
        duplicate_user = make_user_record(
            user_id="U-002",
            email="AGENT@EXAMPLE.COM",
        )

        # 声明重复邮箱必须产生项目异常
        with pytest.raises(
            UserAlreadyExistsError,
            match="用户邮箱已存在",
        ):
            # 尝试保存重复用户
            create_user(
                session,
                duplicate_user,
            )

        # 失败并回滚后，Session 应该仍然可以使用
        stored_first_user = get_user_by_email(
            session,
            "agent@example.com",
        )

        # 原来的用户不能受到影响
        assert stored_first_user is not None
        assert stored_first_user.id == "U-001"

        # 重复用户不能被保存
        stored_duplicate_user = get_user_by_id(
            session,
            "U-002",
        )

        # 回滚后数据库中不存在 U-002
        assert stored_duplicate_user is None

    # 释放测试数据库连接
    test_engine.dispose()
