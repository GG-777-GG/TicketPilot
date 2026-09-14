# UTC 和 datetime 用来构造过期时间
# timedelta 用来把时间向前移动
from datetime import UTC, datetime, timedelta

# PyJWT 用来解析并验证测试中的访问令牌
import jwt

# pytest 用于检查代码是否抛出预期异常
import pytest

# 导入当前应用的 JWT 配置
from ticketpilot.config import settings

# 导入需要测试的密码安全函数
from ticketpilot.security import (
    hash_password,
    verify_password,
    decode_access_token,
    JWT_ALGORITHM,
    create_access_token,
    # 项目统一的无效令牌异常
    InvalidAccessTokenError,
)


# 验证密码不会以明文形式保存
def test_hash_password_does_not_return_plaintext() -> None:
    # 准备用户输入的测试密码
    plain_password = "correct-password-123"

    # 第一次生成密码哈希
    first_hash = hash_password(
        plain_password
    )

    # 对同一个密码再次生成哈希
    second_hash = hash_password(
        plain_password
    )

    # 哈希结果不能等于原始密码
    assert first_hash != plain_password

    # 随机盐会使同一个密码产生不同的哈希
    assert first_hash != second_hash


# 验证正确密码能够通过检查
def test_verify_password_accepts_correct_password() -> None:
    # 准备用户的正确密码
    plain_password = "correct-password-123"

    # 模拟注册时生成并保存密码哈希
    hashed_password = hash_password(
        plain_password
    )

    # 使用正确密码进行验证
    is_valid = verify_password(
        plain_password,
        hashed_password,
    )

    # 正确密码必须验证成功
    assert is_valid is True


# 验证错误密码不能通过检查
def test_verify_password_rejects_wrong_password() -> None:
    # 模拟用户注册时使用的正确密码
    correct_password = "correct-password-123"

    # 模拟用户登录时输入的错误密码
    wrong_password = "wrong-password-456"

    # 生成正确密码对应的数据库哈希
    hashed_password = hash_password(
        correct_password
    )

    # 使用错误密码进行验证
    is_valid = verify_password(
        wrong_password,
        hashed_password,
    )

    # 错误密码必须验证失败
    assert is_valid is False

# 验证生成的 JWT 包含正确用户和过期时间
def test_create_access_token_contains_expected_claims() -> None:
    # 准备一个假的用户 ID
    user_id = "test-user-id"

    # 为这个用户生成访问令牌
    access_token = create_access_token(
        user_id,
    )

    # JWT 应该是可以发送给客户端的字符串
    assert isinstance(
        access_token,
        str,
    )

    # 取得当前应用真正使用的 JWT 密钥
    secret_key = (
        settings.jwt_secret_key.get_secret_value()
    )

    # 使用相同密钥验证签名并解析令牌内容
    payload = jwt.decode(
        access_token,
        secret_key,
        algorithms=[JWT_ALGORITHM],
    )

    # sub 应该保存令牌所属的用户 ID
    assert payload["sub"] == user_id

    # JWT 中应该包含签发时间
    assert "iat" in payload

    # JWT 中应该包含过期时间
    assert "exp" in payload

    # 计算令牌从签发到过期一共有多少秒
    token_lifetime_seconds = (
        payload["exp"] - payload["iat"]
    )

    # 验证有效期与配置中的分钟数完全一致
    assert token_lifetime_seconds == (
        settings.access_token_expire_minutes * 60
    )

# 验证合法 JWT 能够解析出正确的用户 ID
def test_decode_access_token_returns_subject() -> None:
    # 准备要写入 JWT 的模拟用户 ID
    expected_user_id = "test-user-id"

    # 使用服务器的签发函数创建 JWT
    access_token = create_access_token(
        expected_user_id,
    )

    # 使用服务器的解析函数验证 JWT
    actual_user_id = decode_access_token(
        access_token,
    )

    # 解析出来的用户 ID 应该与签发时完全相同
    assert actual_user_id == expected_user_id

# 验证签名被篡改的 JWT 会被拒绝
def test_decode_access_token_rejects_tampered_token() -> None:
    # 先生成一个正常的访问令牌
    access_token = create_access_token(
        "test-user-id",
    )

    # JWT 由 header、payload、signature 三部分组成
    header, payload, signature = (
        access_token.split(".")
    )

    # 保留 header 和 payload
    # 但故意把真正的签名替换成伪造签名
    tampered_token = (
        f"{header}.{payload}.invalid-signature"
    )

    # 解析被篡改的令牌时，应该抛出统一异常
    with pytest.raises(
        InvalidAccessTokenError,
        match="访问令牌无效",
    ):
        # 这里是我们预期会失败的代码
        decode_access_token(
            tampered_token,
        )

# 验证已经过期的 JWT 会被拒绝
def test_decode_access_token_rejects_expired_token() -> None:
    # 获取当前 UTC 时间
    current_time = datetime.now(UTC)

    # 把过期时间设置成一分钟以前
    expired_at = current_time - timedelta(
        minutes=1,
    )

    # 构造一个已经过期的 JWT 数据
    payload = {
        # 令牌所属的模拟用户 ID
        "sub": "test-user-id",

        # 假设令牌在三十一分钟前签发
        "iat": current_time - timedelta(
            minutes=31,
        ),

        # 令牌已经在一分钟前过期
        "exp": expired_at,
    }

    # 获取应用当前使用的 JWT 密钥
    secret_key = (
        settings.jwt_secret_key.get_secret_value()
    )

    # 使用正确密钥签名这个过期令牌
    expired_token = jwt.encode(
        payload,
        secret_key,
        algorithm=JWT_ALGORITHM,
    )

    # 即使签名正确，只要过期也必须抛出异常
    with pytest.raises(
        InvalidAccessTokenError,
        match="访问令牌无效",
    ):
        # 尝试解析已经过期的 JWT
        decode_access_token(
            expired_token,
        )

# 验证缺少 sub 用户 ID 的 JWT 会被拒绝
def test_decode_access_token_rejects_missing_subject() -> None:
    # 获取当前 UTC 时间
    current_time = datetime.now(UTC)

    # 构造一个没有 sub 字段的 JWT 数据
    payload = {
        # 令牌签发时间
        "iat": current_time,

        # 设置成三十分钟后过期
        "exp": current_time + timedelta(
            minutes=30,
        ),

        # 这里故意不提供 sub
    }

    # 获取应用当前使用的 JWT 密钥
    secret_key = (
        settings.jwt_secret_key.get_secret_value()
    )

    # 使用正确密钥生成令牌
    token_without_subject = jwt.encode(
        payload,
        secret_key,
        algorithm=JWT_ALGORITHM,
    )

    # 缺少用户 ID 时应该抛出统一异常
    with pytest.raises(
        InvalidAccessTokenError,
        match="访问令牌无效",
    ):
        # 尝试解析缺少 sub 的 JWT
        decode_access_token(
            token_without_subject,
        )