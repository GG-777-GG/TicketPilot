# 导入密码哈希工具
from pwdlib import PasswordHash

from datetime import UTC,datetime,timedelta

import jwt

# InvalidTokenError 表示 JWT 无效、过期或签名错误
from jwt.exceptions import InvalidTokenError

from ticketpilot.config import settings

# 使用 pwdlib 当前推荐的安全算法
# 当前会使用 Argon2 算法
_password_hasher = PasswordHash.recommended()

JWT_ALGORITHM = "HS256"
# 表示客户端提供的访问令牌无效
class InvalidAccessTokenError(RuntimeError):
    # 当前只需要用异常类型区分错误，不需要额外代码
    pass


# 把用户提交的明文密码转换成安全哈希
def hash_password(
    # 接收用户输入的原始密码
    plain_password: str,
) -> str:
    # 生成包含算法、参数、随机盐和哈希结果的字符串
    return _password_hasher.hash(
        plain_password
    )


# 验证明文密码是否与数据库中的哈希匹配
def verify_password(
    # 接收用户登录时输入的明文密码
    plain_password: str,

    # 接收数据库中保存的密码哈希
    hashed_password: str,
) -> bool:
    # 验证成功返回 True，密码错误返回 False
    return _password_hasher.verify(
        plain_password,
        hashed_password,
    )

# 为登录成功的用户生成访问令牌
def create_access_token(
    # subject 表示令牌属于谁，这里会传入用户 ID
    subject: str,
) -> str:
    # 获取当前 UTC 时间，作为令牌签发时间
    issued_at = datetime.now(UTC)

    # 根据配置的有效分钟数计算过期时间
    expires_at = issued_at + timedelta(
        minutes=settings.access_token_expire_minutes,
    )

    # 定义要写入 JWT 的数据
    payload = {
        # sub 是 subject 的缩写，保存用户 ID
        "sub": subject,

        # iat 是 issued at 的缩写，表示签发时间
        "iat": issued_at,

        # exp 是 expiration 的缩写，表示过期时间
        "exp": expires_at,
    }

    # SecretStr 默认隐藏真实密钥
    # 这里签名时需要取出密钥的真实内容
    secret_key = (
        settings.jwt_secret_key.get_secret_value()
    )

    # 使用密钥和 HS256 算法签名并生成 JWT 字符串
    return jwt.encode(
        payload,
        secret_key,
        algorithm=JWT_ALGORITHM,
    )

# 验证 JWT 并取出其中的用户 ID
def decode_access_token(
    # 接收客户端提交的 JWT 字符串
    access_token: str,
) -> str:
    # 从 SecretStr 中取得真正的 JWT 密钥
    secret_key = (
        settings.jwt_secret_key.get_secret_value()
    )

    # 捕获 JWT 签名错误、格式错误和过期错误
    try:
        # 验证签名和过期时间，并解析令牌内容
        payload = jwt.decode(
            # 要解析的 JWT
            access_token,

            # 使用签发时相同的密钥验证签名
            secret_key,

            # 只允许服务器指定的 HS256 算法
            algorithms=[
                JWT_ALGORITHM,
            ],
        )

    # 任何 PyJWT 令牌错误都会进入这里
    except InvalidTokenError as error:
        # 转换成项目自己的统一异常
        raise InvalidAccessTokenError(
            "访问令牌无效",
        ) from error

    # 从 JWT 中读取 sub，也就是用户 ID
    subject = payload.get("sub")

    # sub 必须是非空字符串
    if not isinstance(
        subject,
        str,
    ) or not subject:
        # 缺少用户 ID 的令牌同样无效
        raise InvalidAccessTokenError(
            "访问令牌无效",
        )

    # 返回通过验证的用户 ID
    return subject