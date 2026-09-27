# UTC、datetime 用于记录令牌签发时间
from datetime import UTC, datetime

# pytest 用于参数化测试
import pytest

# 为测试用户生成真实的 JWT
from ticketpilot.identity.security import create_access_token

# TestClient 用来模拟客户端发送 HTTP 请求
from fastapi.testclient import TestClient

# 导入身份路由模块；测试应替换“函数实际查找名字”的位置。
import ticketpilot.identity.router as api

# 删除权限测试还需要替换工单路由使用的 Repository。
import ticketpilot.tickets.router as ticket_api

# 导入 FastAPI 应用
from ticketpilot.app import app

# 导入用户 ORM 模型，给测试数据提供类型
from ticketpilot.identity.models import UserRecord


# 固定用户创建时间，避免测试结果每次运行都不同
TEST_USER_CREATED_AT = datetime(
    2026,
    1,
    1,
    tzinfo=UTC,
)

# 创建测试客户端，相当于一个模拟的前端
client = TestClient(app)


# 测试用户注册成功时，接口是否返回安全的用户信息
def test_register_user_returns_safe_response(
    monkeypatch,
) -> None:
    # 保存传给密码加密函数的原始密码
    hashed_plain_passwords: list[str] = []

    # 保存准备写入数据库的用户
    saved_users: list[UserRecord] = []

    # 模拟密码加密函数
    def fake_hash_password(
        plain_password: str,
    ) -> str:
        # 记录 API 传进来的原始密码
        hashed_plain_passwords.append(plain_password)

        # 返回一个固定的假密码哈希
        return "hashed-password-for-test"

    # 模拟用户 Repository，不连接真实数据库
    def fake_create_user(
        session,
        user: UserRecord,
    ) -> UserRecord:
        # 模拟 Repository 对邮箱进行标准化
        user.email = user.email.strip().lower()

        # 模拟数据库提供的默认角色
        user.role = "agent"

        # 模拟数据库提供的默认启用状态
        user.is_active = True

        # 模拟数据库生成的创建时间
        user.created_at = TEST_USER_CREATED_AT

        # 记录 API 准备保存的用户
        saved_users.append(user)

        # 模拟 Repository 返回保存后的用户
        return user

    # 把 API 中真正的密码加密函数替换成假函数
    monkeypatch.setattr(
        api,
        "hash_password",
        fake_hash_password,
    )

    # 把真正操作数据库的 create_user 替换成假函数
    monkeypatch.setattr(
        api.user_repository,
        "create_user",
        fake_create_user,
    )

    # 模拟前端向注册接口发送 POST 请求
    response = client.post(
        "/v1/auth/register",
        json={
            "email": "Agent@Example.com",
            "display_name": "  测试客服  ",
            "password": "secure-password",
        },
    )

    # 注册成功应该返回 HTTP 201
    assert response.status_code == 201

    # 把响应 JSON 转换成 Python 字典
    response_data = response.json()

    # 验证原始密码确实传给了密码加密函数
    assert hashed_plain_passwords == [
        "secure-password",
    ]

    # 验证 Repository 只保存了一个用户
    assert len(saved_users) == 1

    # 取出准备保存的用户
    saved_user = saved_users[0]

    # 验证邮箱已经统一转换成小写
    assert saved_user.email == "agent@example.com"

    # 验证显示名称两边的空格已经被删除
    assert saved_user.display_name == "测试客服"

    # 验证数据库中保存的是密码哈希
    assert saved_user.password_hash == "hashed-password-for-test"

    # 验证数据库没有直接保存明文密码
    assert saved_user.password_hash != "secure-password"

    # 验证接口返回了刚刚创建的用户 ID
    assert response_data["id"] == saved_user.id

    # 验证接口返回标准化后的邮箱
    assert response_data["email"] == "agent@example.com"

    # 验证接口返回清理后的显示名称
    assert response_data["display_name"] == "测试客服"

    # 验证普通注册用户的默认角色是 agent
    assert response_data["role"] == "agent"

    # 验证用户默认处于启用状态
    assert response_data["is_active"] is True

    # 验证创建时间正确
    assert (
        datetime.fromisoformat(response_data["created_at"])
        == TEST_USER_CREATED_AT
    )

    # 响应中绝对不能泄露用户的明文密码
    assert "password" not in response_data

    # 响应中也不能泄露密码哈希
    assert "password_hash" not in response_data

# 测试使用已存在的邮箱注册时，接口是否返回 409
def test_register_user_returns_409_when_email_already_exists(
    monkeypatch,
) -> None:
    # 模拟密码加密，避免测试时执行真正的 Argon2 计算
    def fake_hash_password(
        plain_password: str,
    ) -> str:
        # 返回固定的测试密码哈希
        return "hashed-password-for-test"

    # 模拟 Repository 发现邮箱已经存在
    def fake_create_user(
        session,
        user: UserRecord,
    ) -> UserRecord:
        # Repository 主动抛出“用户已存在”异常
        raise api.user_repository.UserAlreadyExistsError(
            "用户邮箱已存在",
        )

    # 替换 API 中真正的密码加密函数
    monkeypatch.setattr(
        api,
        "hash_password",
        fake_hash_password,
    )

    # 替换真正访问数据库的 create_user 函数
    monkeypatch.setattr(
        api.user_repository,
        "create_user",
        fake_create_user,
    )

    # 使用已经存在的邮箱发送注册请求
    response = client.post(
        "/v1/auth/register",
        json={
            "email": "existing@example.com",
            "display_name": "已有用户",
            "password": "secure-password",
        },
    )

    # 邮箱冲突应该返回 HTTP 409
    assert response.status_code == 409

    # 响应内容应该清楚告诉客户端邮箱已经存在
    assert response.json() == {
        "detail": "用户邮箱已存在",
    }

# 测试注册参数不符合要求时，接口是否返回 422
def test_register_user_returns_422_for_invalid_password(
    monkeypatch,
) -> None:
    # 如果这个函数被执行，说明无效请求没有被 Pydantic 拦截
    def should_not_hash_password(
        plain_password: str,
    ) -> str:
        raise AssertionError(
            "无效请求不应该执行密码加密",
        )

    # 如果这个函数被执行，说明无效数据进入了 Repository
    def should_not_create_user(
        session,
        user: UserRecord,
    ) -> UserRecord:
        raise AssertionError(
            "无效请求不应该写入数据库",
        )

    # 替换密码加密函数
    monkeypatch.setattr(
        api,
        "hash_password",
        should_not_hash_password,
    )

    # 替换 Repository 函数
    monkeypatch.setattr(
        api.user_repository,
        "create_user",
        should_not_create_user,
    )

    # 发送密码过短的注册请求
    response = client.post(
        "/v1/auth/register",
        json={
            "email": "agent@example.com",
            "display_name": "测试客服",
            # 密码少于 UserCreateRequest 要求的 8 个字符
            "password": "short",
        },
    )

    # 参数验证失败应该返回 HTTP 422
    assert response.status_code == 422

    # 取出 Pydantic 返回的验证错误列表
    validation_errors = response.json()["detail"]

    # 验证错误确实发生在请求体的 password 字段
    assert any(
        error["loc"] == ["body", "password"]
        for error in validation_errors
    )

# 测试用户存储发生故障时，接口是否返回安全的 500
def test_register_user_returns_500_when_repository_fails(
    monkeypatch,
) -> None:
    # 模拟密码加密函数
    def fake_hash_password(
        plain_password: str,
    ) -> str:
        # 返回固定的测试密码哈希
        return "hashed-password-for-test"

    # 模拟 Repository 或数据库发生故障
    def fake_create_user(
        session,
        user: UserRecord,
    ) -> UserRecord:
        # 这里故意抛出内部错误
        raise RuntimeError(
            "测试数据库连接失败",
        )

    # 替换真正的密码加密函数
    monkeypatch.setattr(
        api,
        "hash_password",
        fake_hash_password,
    )

    # 替换真正访问数据库的 create_user 函数
    monkeypatch.setattr(
        api.user_repository,
        "create_user",
        fake_create_user,
    )

    # 发送一个本身完全合法的注册请求
    response = client.post(
        "/v1/auth/register",
        json={
            "email": "agent@example.com",
            "display_name": "测试客服",
            "password": "secure-password",
        },
    )

    # Repository 故障应该转换成 HTTP 500
    assert response.status_code == 500

    # 客户端只能看到安全、统一的错误信息
    assert response.json() == {
        "detail": "用户服务暂时不可用",
    }

    # 响应中不能泄露真正的数据库错误
    assert "测试数据库连接失败" not in response.text

# 测试邮箱和密码正确时能够成功登录
def test_login_user_returns_access_token(
    monkeypatch,
) -> None:
    # 创建一个模拟的数据库用户
    stored_user = UserRecord(
        # 模拟用户 ID
        id="test-user-id",

        # 模拟数据库中的邮箱
        email="agent@example.com",

        # 模拟用户显示名称
        display_name="测试客服",

        # 数据库中只保存密码哈希
        password_hash="stored-password-hash",

        # 模拟普通客服角色
        role="agent",

        # 模拟账号处于启用状态
        is_active=True,

        # 模拟数据库中的创建时间
        created_at=TEST_USER_CREATED_AT,
    )

    # 记录 Repository 收到的邮箱
    queried_emails: list[str] = []

    # 记录密码验证函数收到的数据
    verified_passwords: list[
        tuple[str, str]
    ] = []

    # 记录生成 JWT 时使用的用户 ID
    token_subjects: list[str] = []

    # 模拟根据邮箱查询用户
    def fake_get_user_by_email(
        session,
        email: str,
    ) -> UserRecord:
        # 记录 API 查询的邮箱
        queried_emails.append(email)

        # 模拟从数据库找到用户
        return stored_user

    # 模拟密码验证成功
    def fake_verify_password(
        plain_password: str,
        hashed_password: str,
    ) -> bool:
        # 记录明文密码和数据库密码哈希
        verified_passwords.append(
            (
                plain_password,
                hashed_password,
            )
        )

        # 返回 True，表示密码正确
        return True

    # 模拟 JWT 生成功能
    def fake_create_access_token(
        subject: str,
    ) -> str:
        # 记录 JWT 中使用的用户 ID
        token_subjects.append(subject)

        # 返回固定测试令牌
        return "test-access-token"

    # 替换真正的数据库查询函数
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    # 替换真正的密码验证函数
    monkeypatch.setattr(
        api,
        "verify_password",
        fake_verify_password,
    )

    # 替换真正的 JWT 生成函数
    monkeypatch.setattr(
        api,
        "create_access_token",
        fake_create_access_token,
    )

    # 向登录接口发送正确邮箱和密码
    response = client.post(
        "/v1/auth/login",
        json={
            "email": "agent@example.com",
            "password": "correct-password",
        },
    )

    # 登录成功应该返回 HTTP 200
    assert response.status_code == 200

    # 验证 API 使用正确的邮箱查询用户
    assert queried_emails == [
        "agent@example.com",
    ]

    # 验证 API 用明文密码匹配数据库密码哈希
    assert verified_passwords == [
        (
            "correct-password",
            "stored-password-hash",
        )
    ]

    # 验证 JWT 使用数据库用户 ID 生成
    assert token_subjects == [
        "test-user-id",
    ]

    # 验证接口返回正确的令牌格式
    assert response.json() == {
        "access_token": "test-access-token",
        "token_type": "bearer",
    }

# 测试登录邮箱不存在时返回 401
def test_login_user_returns_401_when_user_does_not_exist(
    monkeypatch,
) -> None:
    # 模拟 Repository 找不到用户
    def fake_get_user_by_email(
        session,
        email: str,
    ) -> None:
        # None 表示数据库中没有这个邮箱
        return None

    # 用户不存在时，不应该继续验证密码
    def should_not_verify_password(
        plain_password: str,
        hashed_password: str,
    ) -> bool:
        raise AssertionError(
            "用户不存在时不应该验证密码",
        )

    # 身份验证失败时，不应该生成 JWT
    def should_not_create_access_token(
        subject: str,
    ) -> str:
        raise AssertionError(
            "登录失败时不应该生成访问令牌",
        )

    # 替换真正的用户查询函数
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    # 替换真正的密码验证函数
    monkeypatch.setattr(
        api,
        "verify_password",
        should_not_verify_password,
    )

    # 替换真正的 JWT 生成函数
    monkeypatch.setattr(
        api,
        "create_access_token",
        should_not_create_access_token,
    )

    # 使用不存在的邮箱发送登录请求
    response = client.post(
        "/v1/auth/login",
        json={
            "email": "missing@example.com",
            "password": "some-password",
        },
    )

    # 用户不存在应该返回 HTTP 401
    assert response.status_code == 401

    # 不告诉客户端究竟是邮箱还是密码错误
    assert response.json() == {
        "detail": "邮箱或密码错误",
    }

    # 响应头声明本系统使用 Bearer 身份认证
    assert response.headers["www-authenticate"] == "Bearer"

# 测试密码错误时返回 401
def test_login_user_returns_401_when_password_is_wrong(
    monkeypatch,
) -> None:
    # 创建一个模拟的数据库用户
    stored_user = UserRecord(
        # 模拟用户 ID
        id="test-user-id",

        # 模拟已注册邮箱
        email="agent@example.com",

        # 模拟用户显示名称
        display_name="测试客服",

        # 模拟数据库中保存的密码哈希
        password_hash="stored-password-hash",

        # 模拟用户角色
        role="agent",

        # 模拟账号处于启用状态
        is_active=True,

        # 模拟用户创建时间
        created_at=TEST_USER_CREATED_AT,
    )

    # 记录密码验证函数收到的数据
    verified_passwords: list[
        tuple[str, str]
    ] = []

    # 模拟 Repository 成功找到用户
    def fake_get_user_by_email(
        session,
        email: str,
    ) -> UserRecord:
        # 返回数据库中的模拟用户
        return stored_user

    # 模拟密码验证失败
    def fake_verify_password(
        plain_password: str,
        hashed_password: str,
    ) -> bool:
        # 记录参与验证的明文密码和密码哈希
        verified_passwords.append(
            (
                plain_password,
                hashed_password,
            )
        )

        # False 表示密码不匹配
        return False

    # 密码错误时不应该生成 JWT
    def should_not_create_access_token(
        subject: str,
    ) -> str:
        raise AssertionError(
            "密码错误时不应该生成访问令牌",
        )

    # 替换真正的用户查询函数
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    # 替换真正的密码验证函数
    monkeypatch.setattr(
        api,
        "verify_password",
        fake_verify_password,
    )

    # 替换真正的 JWT 生成函数
    monkeypatch.setattr(
        api,
        "create_access_token",
        should_not_create_access_token,
    )

    # 使用错误密码发送登录请求
    response = client.post(
        "/v1/auth/login",
        json={
            "email": "agent@example.com",
            "password": "wrong-password",
        },
    )

    # 密码错误应该返回 HTTP 401
    assert response.status_code == 401

    # 验证密码检查使用了正确的数据
    assert verified_passwords == [
        (
            "wrong-password",
            "stored-password-hash",
        )
    ]

    # 与邮箱不存在使用相同的错误信息
    assert response.json() == {
        "detail": "邮箱或密码错误",
    }

    # 响应头声明 Bearer 认证方式
    assert response.headers["www-authenticate"] == "Bearer"

# 测试已停用的用户不能登录
def test_login_user_returns_403_when_user_is_inactive(
    monkeypatch,
) -> None:
    # 创建一个已停用的模拟用户
    stored_user = UserRecord(
        # 模拟用户 ID
        id="inactive-user-id",

        # 模拟已注册邮箱
        email="inactive@example.com",

        # 模拟用户显示名称
        display_name="停用用户",

        # 模拟数据库密码哈希
        password_hash="stored-password-hash",

        # 模拟用户角色
        role="agent",

        # False 表示账号已经被停用
        is_active=False,

        # 模拟用户创建时间
        created_at=TEST_USER_CREATED_AT,
    )

    # 记录是否执行了密码验证
    verified_passwords: list[
        tuple[str, str]
    ] = []

    # 模拟 Repository 找到已停用用户
    def fake_get_user_by_email(
        session,
        email: str,
    ) -> UserRecord:
        # 返回已停用的模拟用户
        return stored_user

    # 模拟用户输入了正确密码
    def fake_verify_password(
        plain_password: str,
        hashed_password: str,
    ) -> bool:
        # 记录密码验证数据
        verified_passwords.append(
            (
                plain_password,
                hashed_password,
            )
        )

        # True 表示密码正确
        return True

    # 停用账号不能生成 JWT
    def should_not_create_access_token(
        subject: str,
    ) -> str:
        raise AssertionError(
            "停用账号不应该生成访问令牌",
        )

    # 替换数据库用户查询函数
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    # 替换密码验证函数
    monkeypatch.setattr(
        api,
        "verify_password",
        fake_verify_password,
    )

    # 替换 JWT 生成函数
    monkeypatch.setattr(
        api,
        "create_access_token",
        should_not_create_access_token,
    )

    # 使用正确邮箱和密码尝试登录
    response = client.post(
        "/v1/auth/login",
        json={
            "email": "inactive@example.com",
            "password": "correct-password",
        },
    )

    # 已认证但被禁止使用，应该返回 HTTP 403
    assert response.status_code == 403

    # 验证系统确实先检查了密码
    assert verified_passwords == [
        (
            "correct-password",
            "stored-password-hash",
        )
    ]

    # 返回账号停用提示
    assert response.json() == {
        "detail": "用户账号已停用",
    }

# 测试查询用户失败时返回安全的 500
def test_login_user_returns_500_when_repository_fails(
    monkeypatch,
) -> None:
    # 模拟 Repository 查询数据库失败
    def fake_get_user_by_email(
        session,
        email: str,
    ) -> None:
        # 故意抛出内部数据库错误
        raise RuntimeError(
            "测试用户数据库连接失败",
        )

    # 数据库查询失败后不应该验证密码
    def should_not_verify_password(
        plain_password: str,
        hashed_password: str,
    ) -> bool:
        raise AssertionError(
            "数据库查询失败后不应该验证密码",
        )

    # 数据库查询失败后不应该生成 JWT
    def should_not_create_access_token(
        subject: str,
    ) -> str:
        raise AssertionError(
            "数据库查询失败后不应该生成访问令牌",
        )

    # 替换真正的用户查询函数
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    # 替换真正的密码验证函数
    monkeypatch.setattr(
        api,
        "verify_password",
        should_not_verify_password,
    )

    # 替换真正的 JWT 生成函数
    monkeypatch.setattr(
        api,
        "create_access_token",
        should_not_create_access_token,
    )

    # 发送一个格式正确的登录请求
    response = client.post(
        "/v1/auth/login",
        json={
            "email": "agent@example.com",
            "password": "correct-password",
        },
    )

    # Repository 故障应该转换成 HTTP 500
    assert response.status_code == 500

    # 客户端只能收到统一、安全的错误信息
    assert response.json() == {
        "detail": "用户服务暂时不可用",
    }

    # 响应中不能泄露数据库内部错误
    assert (
        "测试用户数据库连接失败"
        not in response.text
    )

# 测试登录参数格式错误时返回 422
def test_login_user_returns_422_for_invalid_email(
    monkeypatch,
) -> None:
    # 参数验证失败时不应该查询数据库
    def should_not_get_user_by_email(
        session,
        email: str,
    ) -> None:
        raise AssertionError(
            "无效请求不应该查询用户数据库",
        )

    # 参数验证失败时不应该验证密码
    def should_not_verify_password(
        plain_password: str,
        hashed_password: str,
    ) -> bool:
        raise AssertionError(
            "无效请求不应该验证密码",
        )

    # 参数验证失败时不应该生成 JWT
    def should_not_create_access_token(
        subject: str,
    ) -> str:
        raise AssertionError(
            "无效请求不应该生成访问令牌",
        )

    # 替换用户查询函数
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_email",
        should_not_get_user_by_email,
    )

    # 替换密码验证函数
    monkeypatch.setattr(
        api,
        "verify_password",
        should_not_verify_password,
    )

    # 替换 JWT 生成函数
    monkeypatch.setattr(
        api,
        "create_access_token",
        should_not_create_access_token,
    )

    # 使用格式错误的邮箱发送登录请求
    response = client.post(
        "/v1/auth/login",
        json={
            # 这不是合法的邮箱地址
            "email": "not-an-email",

            # 密码本身格式正确
            "password": "correct-password",
        },
    )

    # Pydantic 验证失败应该返回 HTTP 422
    assert response.status_code == 422

    # 取得 FastAPI 返回的验证错误列表
    validation_errors = response.json()["detail"]

    # 验证错误发生在请求体的 email 字段
    assert any(
        error["loc"] == ["body", "email"]
        for error in validation_errors
    )

# 测试未携带 JWT 时不能查询当前用户
def test_get_me_returns_401_without_token() -> None:
    # 模拟客户端访问“当前用户”接口
    # 没有提供 Authorization 请求头
    response = client.get(
        "/v1/auth/me",
    )

    # 缺少登录凭证，应该返回 HTTP 401
    assert response.status_code == 401

    # 验证接口返回统一的认证错误提示
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }

    # 告诉客户端需要使用 Bearer 认证方式
    assert response.headers["www-authenticate"] == "Bearer"

# 测试携带有效 JWT 时能够查询当前用户
def test_get_me_returns_current_user(
    monkeypatch,
) -> None:
    # 构造模拟用户，不会真正写入数据库
    stored_user = UserRecord(
        # 用户的唯一 ID，稍后写入 JWT
        id="test-user-id",

        # 用户登录邮箱
        email="agent@example.com",

        # 用户显示名称
        display_name="测试客服",

        # 模拟数据库保存的密码哈希
        password_hash="stored-password-hash",

        # 用户角色
        role="agent",

        # 账号必须处于启用状态
        is_active=True,

        # 使用文件中已有的固定时间
        created_at=TEST_USER_CREATED_AT,
    )

    # 记录认证过程查询了哪些用户 ID
    queried_user_ids: list[str] = []

    # 模拟 Repository 根据用户 ID 查询数据库
    def fake_get_user_by_id(
        session,
        user_id: str,
    ) -> UserRecord:
        # 记录从 JWT 中解析出来的用户 ID
        queried_user_ids.append(user_id)

        # 返回模拟的数据库用户
        return stored_user

    # 替换用户 Repository 的查询函数
    # auth 模块也使用同一个 user_repository 模块
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    # 使用真实函数，为模拟用户生成 JWT
    access_token = create_access_token(
        stored_user.id,
    )

    # 携带 JWT 访问当前用户接口
    response = client.get(
        "/v1/auth/me",

        # 将 JWT 放到 Authorization 请求头中
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )

    # 身份验证成功，应该返回 HTTP 200
    assert response.status_code == 200

    # 确认使用 JWT 中的用户 ID 查询了数据库
    assert queried_user_ids == [
        "test-user-id",
    ]

    # 把响应 JSON 转换成 Python 字典
    response_data = response.json()

    # 验证返回的用户 ID
    assert response_data["id"] == "test-user-id"

    # 验证返回的邮箱
    assert response_data["email"] == "agent@example.com"

    # 验证返回的显示名称
    assert response_data["display_name"] == "测试客服"

    # 验证返回的用户角色
    assert response_data["role"] == "agent"

    # 验证返回的账号状态
    assert response_data["is_active"] is True

    # 把响应中的时间字符串转回 datetime，再检查是否正确
    assert (
        datetime.fromisoformat(response_data["created_at"])
        == TEST_USER_CREATED_AT
    )

    # 响应中不能包含明文密码
    assert "password" not in response_data

    # 响应中也不能包含密码哈希
    assert "password_hash" not in response_data

# 测试携带无效 JWT 时不能查询当前用户
def test_get_me_returns_401_for_invalid_token(
    monkeypatch,
) -> None:
    # JWT 验证失败后，不应该进入数据库查询
    def should_not_get_user_by_id(
        session,
        user_id: str,
    ) -> UserRecord:
        # 如果执行到这里，就让测试失败
        raise AssertionError(
            "无效 JWT 不应该触发用户查询",
        )

    # 替换 Repository，检查认证失败后是否停止查询
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_id",
        should_not_get_user_by_id,
    )

    # 携带一个格式无效的 JWT 访问接口
    response = client.get(
        "/v1/auth/me",

        # Bearer 认证方式正确，但后面的令牌无效
        headers={
            "Authorization": "Bearer invalid-access-token",
        },
    )

    # JWT 验证失败应该返回 HTTP 401
    assert response.status_code == 401

    # 返回统一提示，不泄露令牌解析的内部错误
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }

    # 响应头声明 Bearer 认证方式
    assert response.headers["www-authenticate"] == "Bearer"

# 测试已停用用户不能通过 JWT 获取当前用户信息
def test_get_me_returns_403_for_inactive_user(
    monkeypatch,
) -> None:
    # 模拟数据库中已经被停用的用户
    inactive_user = UserRecord(
        # 用户唯一 ID
        id="inactive-user-id",

        # 用户登录邮箱
        email="inactive@example.com",

        # 用户显示名称
        display_name="停用用户",

        # 模拟数据库保存的密码哈希
        password_hash="stored-password-hash",

        # 用户角色
        role="agent",

        # 关键条件：账号当前已经停用
        is_active=False,

        # 使用已有的固定创建时间
        created_at=TEST_USER_CREATED_AT,
    )

    # 模拟根据 JWT 中的用户 ID 查询数据库
    def fake_get_user_by_id(
        session,
        user_id: str,
    ) -> UserRecord:
        # 确认查询的是这个停用用户
        assert user_id == inactive_user.id

        # 返回数据库中的最新账号状态
        return inactive_user

    # 替换真实的数据库查询函数
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    # 为该用户 ID 构造一个签名正确、尚未过期的 JWT
    # 这个工具函数只负责签发，不会检查数据库账号状态
    access_token = create_access_token(
        inactive_user.id,
    )

    # 携带有效 JWT 访问当前用户接口
    response = client.get(
        "/v1/auth/me",

        # 按 Bearer 格式传递 JWT
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )

    # 身份已确认，但账号被停用，所以返回 403
    assert response.status_code == 403

    # 验证客户端收到账号停用提示
    assert response.json() == {
        "detail": "用户账号已停用",
    }

# 测试普通客服不能通过 API 删除工单
def test_delete_ticket_returns_403_for_agent(
    # monkeypatch 用来替换真实数据库函数
    monkeypatch,
) -> None:
    # 创建一个已经登录的普通客服用户
    agent_user = UserRecord(
        # 模拟用户 ID
        id="agent-user-id",

        # 模拟用户邮箱
        email="agent@example.com",

        # 模拟用户显示名称
        display_name="普通客服",

        # 本测试不验证密码
        password_hash="stored-password-hash",

        # 关键条件：用户角色是 agent
        role="agent",

        # 用户账号处于启用状态
        is_active=True,

        # 使用测试文件中已有的固定时间
        created_at=TEST_USER_CREATED_AT,
    )

    # 模拟根据 JWT 中的用户 ID 查询当前用户
    def fake_get_user_by_id(
        session,
        user_id: str,
    ) -> UserRecord:
        # 确认 JWT 中保存的是测试用户 ID
        assert user_id == agent_user.id

        # 返回当前普通客服用户
        return agent_user

    # 定义一个绝对不应该执行的工单查询函数
    def should_not_get_ticket_by_id(
        session,
        ticket_id: str,
    ) -> None:
        # 如果执行到这里，说明权限检查没有提前拦截请求
        raise AssertionError(
            "agent 没有删除权限，不应该查询工单",
        )

    # 替换真实的用户数据库查询
    monkeypatch.setattr(
        api.user_repository,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    # 替换工单查询函数，确认删除逻辑不会执行
    monkeypatch.setattr(
        ticket_api.ticket_repository,
        "get_ticket_by_id",
        should_not_get_ticket_by_id,
    )

    # 为普通客服用户生成一个真实有效的 JWT
    access_token = create_access_token(
        agent_user.id,
    )

    # 携带 JWT 请求删除工单
    response = client.delete(
        "/v1/tickets/test-ticket-id",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )

    # 用户已经登录，但角色权限不足
    assert response.status_code == 403

    # 验证统一的权限错误提示
    assert response.json() == {
        "detail": "权限不足",
    }

# 使用同一套断言检查所有工单接口都要求登录
@pytest.mark.parametrize(
    # 每组数据包含请求方法、接口地址和请求体
    (
        "method",
        "path",
        "request_body",
    ),
    [
        # 查询工单列表
        (
            "GET",
            "/v1/tickets",
            None,
        ),

        # 查询单张工单
        (
            "GET",
            "/v1/tickets/test-ticket-id",
            None,
        ),

        # 创建工单
        (
            "POST",
            "/v1/tickets",
            {
                "customer_name": "GG",
                "contact_email": "gg@example.com",
                "channel": "web",
                "subject": "测试工单",
                "description": "验证创建接口需要登录",
                "priority": "medium",
            },
        ),

        # 修改工单状态
        (
            "PATCH",
            "/v1/tickets/test-ticket-id/status",
            {
                "status": "resolved",
            },
        ),

        # 修改工单处理人
        (
            "PATCH",
            "/v1/tickets/test-ticket-id/assignee",
            {
                "assigned_to": "测试客服",
            },
        ),

        # 删除工单
        (
            "DELETE",
            "/v1/tickets/test-ticket-id",
            None,
        ),
    ],
)
def test_ticket_endpoints_require_authentication(
    # 当前测试使用的 HTTP 方法
    method: str,

    # 当前测试使用的接口地址
    path: str,

    # 没有请求体时为 None
    request_body: dict[str, str] | None,
) -> None:
    # 不提供 Authorization 请求头发送请求
    response = client.request(
        method,
        path,
        json=request_body,
    )

    # 所有工单接口都应该拒绝未登录请求
    assert response.status_code == 401

    # 返回统一认证错误
    assert response.json() == {
        "detail": "无效或缺少访问令牌",
    }

    # 声明接口使用 Bearer Token
    assert response.headers["www-authenticate"] == "Bearer"
