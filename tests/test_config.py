# 导入 pytest，用于参数化测试和异常验证
import pytest

# 导入 Pydantic 配置验证失败时抛出的异常
from pydantic import ValidationError

# 导入默认数据库地址和配置类
from ticketpilot.core.config import (
    DEFAULT_DATABASE_URL,
    Settings,
)


# 验证没有外部配置时，会使用默认 SQLite 地址
def test_settings_uses_default_database_url(
        # monkeypatch 用来临时修改测试运行环境
        monkeypatch,
) -> None:
    # 临时删除大写的数据库环境变量
    # raising=False 表示环境变量不存在时也不要报错
    monkeypatch.delenv(
        "DATABASE_URL",
        raising=False,
    )

    # 同时删除可能存在的小写环境变量
    # 这样测试在 Windows 和 Linux 上结果都一致
    monkeypatch.delenv(
        "database_url",
        raising=False,
    )

    # 创建一个专门用于本次测试的配置对象
    # _env_file=None 表示不要读取电脑上真实的 .env 文件
    test_settings = Settings(
        _env_file=None,
    )

    # 验证最后得到的是代码中定义的默认 SQLite 地址
    assert (
        test_settings.database_url
        == DEFAULT_DATABASE_URL
    )


# 验证环境变量可以覆盖默认数据库地址
def test_settings_reads_database_url_from_environment(
        # pytest 会自动提供 monkeypatch
        monkeypatch,
) -> None:
    # 删除可能存在的小写环境变量，避免干扰测试
    monkeypatch.delenv(
        "database_url",
        raising=False,
    )

    # 准备一个只用于测试的数据库地址
    expected_url = "sqlite:///environment-test.db"

    # 临时设置系统环境变量 DATABASE_URL
    monkeypatch.setenv(
        "DATABASE_URL",
        expected_url,
    )

    # 创建新的配置对象
    # 不读取真实 .env，只读取刚才设置的环境变量
    test_settings = Settings(
        _env_file=None,
    )

    # 验证环境变量成功覆盖了默认值
    assert test_settings.database_url == expected_url

# 验证环境变量可以覆盖 JWT 配置
def test_settings_reads_jwt_configuration_from_environment(
    # monkeypatch 用来临时修改环境变量
    monkeypatch,
) -> None:
    # 删除可能存在的小写密钥变量
    monkeypatch.delenv(
        "jwt_secret_key",
        raising=False,
    )

    # 删除可能存在的小写有效期变量
    monkeypatch.delenv(
        "access_token_expire_minutes",
        raising=False,
    )

    # 准备一个长度超过 32 字符的测试密钥
    expected_secret = (
        "test-jwt-secret-key-with-more-than-32-characters"
    )

    # 设置本次测试使用的 JWT 密钥
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        expected_secret,
    )

    # 环境变量中的值是字符串
    monkeypatch.setenv(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "45",
    )

    # 创建只读取环境变量、不读取真实 .env 的配置对象
    test_settings = Settings(
        _env_file=None,
    )

    # SecretStr 需要通过 get_secret_value() 取得真实内容
    assert (
        test_settings.jwt_secret_key.get_secret_value()
        == expected_secret
    )

    # Pydantic 应该把字符串 "45" 转换成整数 45
    assert (
        test_settings.access_token_expire_minutes
        == 45
    )

# 使用三种非法值重复运行测试
@pytest.mark.parametrize(
    # 每轮测试把一个值传给 invalid_minutes
    "invalid_minutes",
    [
        # 小于允许的最小值 1
        "0",

        # 大于允许的最大值 1440
        "1441",

        # 不能转换成整数
        "not-a-number",
    ],
)
# 验证非法令牌有效期会被配置模型拒绝
def test_settings_rejects_invalid_token_expiry(
    # 接收当前测试的非法值
    invalid_minutes: str,

    # 用于临时设置环境变量
    monkeypatch,
) -> None:
    # 删除可能存在的小写环境变量
    monkeypatch.delenv(
        "access_token_expire_minutes",
        raising=False,
    )

    # 设置当前非法值
    monkeypatch.setenv(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        invalid_minutes,
    )

    # 声明创建 Settings 时必须抛出验证异常
    with pytest.raises(
        ValidationError
    ):
        # 不读取真实 .env，保证测试结果稳定
        Settings(
            _env_file=None,
        )
