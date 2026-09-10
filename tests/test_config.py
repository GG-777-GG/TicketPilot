# 导入默认数据库地址和配置类
from ticketpilot.config import (
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