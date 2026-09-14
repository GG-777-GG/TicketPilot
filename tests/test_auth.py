# pytest 用来检查预期异常
import pytest

# HTTPException 是 FastAPI 返回的 HTTP 错误
from fastapi import HTTPException

# 导入测试用户创建时间需要的工具
from datetime import UTC, datetime

# 导入 Bearer 请求头解析后的数据类型
from fastapi.security import (
    HTTPAuthorizationCredentials,
)

# 导入 SQLAlchemy Session
from sqlalchemy.orm import Session

# 导入整个 auth 模块，方便 monkeypatch
import ticketpilot.auth as auth

# 导入用户 ORM 模型
from ticketpilot.db_models import UserRecord


# 测试有效令牌能够取得当前用户
def test_get_current_user_returns_active_user(
    monkeypatch,
) -> None:
    # 创建一个模拟的数据库用户
    stored_user = UserRecord(
        # 模拟用户 ID
        id="test-user-id",

        # 模拟登录邮箱
        email="agent@example.com",

        # 模拟显示名称
        display_name="测试客服",

        # 模拟数据库密码哈希
        password_hash="stored-password-hash",

        # 模拟普通客服角色
        role="agent",

        # True 表示账号处于启用状态
        is_active=True,

        # 模拟用户创建时间
        created_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
    )

    # 记录传给 JWT 解析函数的令牌
    decoded_tokens: list[str] = []

    # 记录 Repository 查询的用户 ID
    queried_user_ids: list[str] = []

    # 模拟 JWT 验证成功
    def fake_decode_access_token(
        access_token: str,
    ) -> str:
        # 记录收到的 JWT
        decoded_tokens.append(
            access_token
        )

        # 模拟从 JWT 的 sub 中取得用户 ID
        return "test-user-id"

    # 模拟 Repository 根据 ID 找到用户
    def fake_get_user_by_id(
        session: Session,
        user_id: str,
    ) -> UserRecord:
        # 记录 API 查询的用户 ID
        queried_user_ids.append(
            user_id
        )

        # 返回模拟用户
        return stored_user

    # 替换真正的 JWT 解析函数
    monkeypatch.setattr(
        auth,
        "decode_access_token",
        fake_decode_access_token,
    )

    # 替换真正的数据库查询函数
    monkeypatch.setattr(
        auth.user_repository,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    # 模拟已经从 Authorization 请求头解析出的数据
    credentials = HTTPAuthorizationCredentials(
        # 请求使用 Bearer 认证方式
        scheme="Bearer",

        # Bearer 后面的模拟 JWT
        credentials="valid-access-token",
    )

    # 创建一个测试 Session
    # Repository 已被替换，所以它不会连接真实数据库
    with Session() as session:
        # 调用被测试的认证函数
        current_user = auth.get_current_user(
            credentials,
            session,
        )

    # 验证认证函数返回数据库中的同一个用户对象
    assert current_user is stored_user

    # 验证正确的 JWT 被送去解析
    assert decoded_tokens == [
        "valid-access-token",
    ]

    # 验证使用 JWT 中的用户 ID 查询数据库
    assert queried_user_ids == [
        "test-user-id",
    ]

# 测试缺少认证信息时返回 401
def test_get_current_user_rejects_missing_credentials() -> None:
    # 创建测试 Session
    with Session() as session:
        # 预期 get_current_user 抛出 HTTPException
        with pytest.raises(
            HTTPException,
        ) as exception_info:
            # None 表示请求没有 Authorization 请求头
            auth.get_current_user(
                None,
                session,
            )

    # 取出实际抛出的 HTTPException
    error = exception_info.value

    # 缺少身份信息应该返回 HTTP 401
    assert error.status_code == 401

    # 验证返回统一的安全提示
    assert error.detail == "无效或缺少访问令牌"

    # 验证响应头声明 Bearer 认证方式
    assert error.headers == {
        "WWW-Authenticate": "Bearer",
    }

# 测试无效 JWT 会被转换成 HTTP 401
def test_get_current_user_rejects_invalid_token(
    monkeypatch,
) -> None:
    # 记录数据库查询是否被错误执行
    queried_user_ids: list[str] = []

    # 模拟 JWT 验证失败
    def fake_decode_access_token(
        access_token: str,
    ) -> str:
        # 故意抛出项目统一的无效令牌异常
        raise auth.InvalidAccessTokenError(
            "访问令牌无效",
        )

    # JWT 无效时不应该查询数据库
    def should_not_get_user_by_id(
        session: Session,
        user_id: str,
    ) -> UserRecord:
        # 如果执行到这里，说明认证流程没有及时停止
        queried_user_ids.append(
            user_id
        )

        # 主动让测试失败
        raise AssertionError(
            "无效 JWT 不应该查询用户数据库",
        )

    # 替换真正的 JWT 解析函数
    monkeypatch.setattr(
        auth,
        "decode_access_token",
        fake_decode_access_token,
    )

    # 替换真正的数据库查询函数
    monkeypatch.setattr(
        auth.user_repository,
        "get_user_by_id",
        should_not_get_user_by_id,
    )

    # 模拟客户端发送的 Bearer Token
    credentials = HTTPAuthorizationCredentials(
        # 认证方式正确
        scheme="Bearer",

        # 但 JWT 内容无效
        credentials="invalid-access-token",
    )

    # 创建测试 Session
    with Session() as session:
        # 预期认证函数抛出 HTTPException
        with pytest.raises(
            HTTPException,
        ) as exception_info:
            # 尝试使用无效 JWT 获取当前用户
            auth.get_current_user(
                credentials,
                session,
            )

    # 取出实际 HTTP 错误
    error = exception_info.value

    # 无效 JWT 应该返回 HTTP 401
    assert error.status_code == 401

    # 返回统一信息，不暴露具体是过期还是签名错误
    assert error.detail == "无效或缺少访问令牌"

    # 响应头声明 Bearer 认证方式
    assert error.headers == {
        "WWW-Authenticate": "Bearer",
    }

    # 确认无效 JWT 没有触发数据库查询
    assert queried_user_ids == []

# 测试 JWT 对应的用户不存在时返回 401
def test_get_current_user_rejects_missing_user(
    monkeypatch,
) -> None:
    # 记录解析过的 JWT
    decoded_tokens: list[str] = []

    # 记录查询过的用户 ID
    queried_user_ids: list[str] = []

    # 模拟 JWT 验证成功
    def fake_decode_access_token(
        access_token: str,
    ) -> str:
        # 记录传入的 JWT
        decoded_tokens.append(
            access_token
        )

        # 模拟 JWT 中包含一个用户 ID
        return "deleted-user-id"

    # 模拟数据库中找不到对应用户
    def fake_get_user_by_id(
        session: Session,
        user_id: str,
    ) -> None:
        # 记录 Repository 查询的用户 ID
        queried_user_ids.append(
            user_id
        )

        # None 表示用户不存在
        return None

    # 替换真正的 JWT 解析函数
    monkeypatch.setattr(
        auth,
        "decode_access_token",
        fake_decode_access_token,
    )

    # 替换真正的用户查询函数
    monkeypatch.setattr(
        auth.user_repository,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    # 模拟客户端发送一个签名有效的 Bearer Token
    credentials = HTTPAuthorizationCredentials(
        # 使用 Bearer 认证方式
        scheme="Bearer",

        # 模拟有效 JWT
        credentials="valid-access-token",
    )

    # 创建测试 Session
    with Session() as session:
        # 预期认证失败并抛出 HTTPException
        with pytest.raises(
            HTTPException,
        ) as exception_info:
            # 尝试根据 JWT 获取当前用户
            auth.get_current_user(
                credentials,
                session,
            )

    # 取出实际 HTTP 错误
    error = exception_info.value

    # JWT 对应用户不存在时应该返回 401
    assert error.status_code == 401

    # 返回统一的令牌错误信息
    assert error.detail == "无效或缺少访问令牌"

    # 响应头声明 Bearer 认证
    assert error.headers == {
        "WWW-Authenticate": "Bearer",
    }

    # 验证传入的 JWT 被正确解析
    assert decoded_tokens == [
        "valid-access-token",
    ]

    # 验证使用 JWT 中的用户 ID 查询了数据库
    assert queried_user_ids == [
        "deleted-user-id",
    ]

# 测试已停用用户不能作为当前用户
def test_get_current_user_rejects_inactive_user(
    monkeypatch,
) -> None:
    # 创建一个已经停用的模拟用户
    inactive_user = UserRecord(
        # 模拟用户 ID
        id="inactive-user-id",

        # 模拟登录邮箱
        email="inactive@example.com",

        # 模拟用户显示名称
        display_name="停用用户",

        # 模拟数据库密码哈希
        password_hash="stored-password-hash",

        # 模拟用户角色
        role="agent",

        # False 表示账号已经停用
        is_active=False,

        # 模拟用户创建时间
        created_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
    )

    # 模拟 JWT 验证成功并返回用户 ID
    def fake_decode_access_token(
        access_token: str,
    ) -> str:
        # 返回 JWT 中模拟的 sub
        return "inactive-user-id"

    # 模拟 Repository 找到已停用用户
    def fake_get_user_by_id(
        session: Session,
        user_id: str,
    ) -> UserRecord:
        # 返回已停用的用户
        return inactive_user

    # 替换真正的 JWT 解析函数
    monkeypatch.setattr(
        auth,
        "decode_access_token",
        fake_decode_access_token,
    )

    # 替换真正的数据库查询函数
    monkeypatch.setattr(
        auth.user_repository,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    # 模拟客户端提供的 Bearer Token
    credentials = HTTPAuthorizationCredentials(
        # 使用正确的 Bearer 认证方式
        scheme="Bearer",

        # 模拟签名有效的 JWT
        credentials="valid-access-token",
    )

    # 创建测试 Session
    with Session() as session:
        # 预期停用用户会触发 HTTPException
        with pytest.raises(
            HTTPException,
        ) as exception_info:
            # 尝试获取当前用户
            auth.get_current_user(
                credentials,
                session,
            )

    # 取出实际抛出的 HTTP 错误
    error = exception_info.value

    # 用户身份有效但账号被禁止，所以返回 403
    assert error.status_code == 403

    # 验证账号停用提示
    assert error.detail == "用户账号已停用"

# 测试数据库查询故障会被转换成 HTTP 500
def test_get_current_user_returns_500_when_repository_fails(
    monkeypatch,
) -> None:
    # 模拟 JWT 验证成功
    def fake_decode_access_token(
        access_token: str,
    ) -> str:
        # 返回 JWT 中的模拟用户 ID
        return "test-user-id"

    # 模拟 Repository 查询数据库时发生故障
    def fake_get_user_by_id(
        session: Session,
        user_id: str,
    ) -> UserRecord:
        # 主动抛出数据库故障对应的异常
        raise RuntimeError(
            "测试数据库连接失败",
        )

    # 替换 JWT 解析函数，让测试进入数据库查询阶段
    monkeypatch.setattr(
        auth,
        "decode_access_token",
        fake_decode_access_token,
    )

    # 替换数据库查询函数，模拟查询故障
    monkeypatch.setattr(
        auth.user_repository,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    # 模拟从请求头中解析出的 Bearer 认证信息
    credentials = HTTPAuthorizationCredentials(
        # 指定 Bearer 认证方式
        scheme="Bearer",

        # 提供模拟 JWT
        credentials="valid-access-token",
    )

    # 创建测试 Session，退出 with 时自动关闭
    with Session() as session:
        # 预期认证函数抛出 HTTPException
        with pytest.raises(
            HTTPException,
        ) as exception_info:
            # 调用认证函数，触发模拟数据库故障
            auth.get_current_user(
                credentials,
                session,
            )

    # 取出认证函数实际抛出的 HTTP 异常
    error = exception_info.value

    # 数据库故障应该返回 500
    assert error.status_code == 500

    # 对外只提供统一提示，不包含数据库内部错误
    assert error.detail == "用户服务暂时不可用"

# 测试普通客服不能通过主管或管理员权限检查
def test_require_roles_rejects_user_without_allowed_role() -> None:
    # 创建一个普通客服用户
    current_user = UserRecord(
        # 模拟用户 ID
        id="agent-user-id",

        # 模拟登录邮箱
        email="agent@example.com",

        # 模拟显示名称
        display_name="普通客服",

        # 本测试不验证密码，所以使用模拟哈希
        password_hash="test-password-hash",

        # 当前用户是普通客服
        role="agent",

        # 账号处于启用状态
        is_active=True,

        # 模拟用户创建时间
        created_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
    )

    # 创建只允许 supervisor 和 admin 通过的检查函数
    permission_checker = auth.require_roles(
        "supervisor",
        "admin",
    )

    # 预期普通客服会被权限检查拒绝
    with pytest.raises(
        HTTPException,
    ) as exception_info:
        # 这里才是真正执行角色检查
        permission_checker(
            current_user,
        )

    # 取得实际抛出的 HTTPException
    error = exception_info.value

    # 已经登录但权限不足，所以应该返回 403
    assert error.status_code == 403

    # 检查返回的错误信息
    assert error.detail == "权限不足"

# 使用两个不同角色重复执行下面的测试
@pytest.mark.parametrize(
    # 这个名字必须与测试函数参数 allowed_role 一致
    "allowed_role",

    # pytest 会依次把这两个值传入测试函数
    [
        "supervisor",
        "admin",
    ],
)
# 测试主管和管理员都能通过权限检查
def test_require_roles_accepts_allowed_role(
    # pytest 自动传入当前正在测试的角色
    allowed_role: str,
) -> None:
    # 创建具有允许角色的模拟用户
    current_user = UserRecord(
        # 模拟用户 ID
        id=f"{allowed_role}-user-id",

        # 模拟用户邮箱
        email=f"{allowed_role}@example.com",

        # 模拟显示名称
        display_name="有权限的用户",

        # 本测试不验证密码
        password_hash="test-password-hash",

        # 第一次是 supervisor，第二次是 admin
        role=allowed_role,

        # 账号处于启用状态
        is_active=True,

        # 模拟用户创建时间
        created_at=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
    )

    # 创建只允许主管和管理员通过的检查函数
    permission_checker = auth.require_roles(
        "supervisor",
        "admin",
    )

    # 执行角色权限检查
    returned_user = permission_checker(
        current_user,
    )

    # 验证权限函数返回的就是原来的用户对象
    assert returned_user is current_user