# 导入 Path，用来安全地处理 Windows 和 Linux 文件路径
from pathlib import Path

# Field 用来给配置字段设置默认值和验证规则
from pydantic import Field

# BaseSettings 负责从环境变量和 .env 文件读取配置
# SettingsConfigDict 负责设置读取配置的规则
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


# __file__ 是当前 config.py 文件的位置
# 第一个 parent 得到 ticketpilot 目录
# 第二个 parent 得到项目根目录 GG-first-agent
BASE_DIR = Path(__file__).resolve().parent.parent

# 默认的 SQLite 数据库文件位置：
# D:\GG-first-agent\data\ticketpilot.db
DEFAULT_DATABASE_FILE = (
    BASE_DIR / "data" / "ticketpilot.db"
)

# 把数据库文件路径转换成 SQLAlchemy 能识别的数据库地址
# 最终类似：sqlite:///D:/GG-first-agent/data/ticketpilot.db
DEFAULT_DATABASE_URL = (
    f"sqlite:///{DEFAULT_DATABASE_FILE.as_posix()}"
)


# 集中定义 TicketPilot 的所有环境配置
# 类似 Spring Boot 中的 @ConfigurationProperties
class Settings(BaseSettings):
    # 配置 BaseSettings 应该怎样读取 .env 文件
    model_config = SettingsConfigDict(
        # 从项目根目录下的 .env 文件读取配置
        env_file=BASE_DIR / ".env",

        # 使用 UTF-8 读取，避免中文出现乱码
        env_file_encoding="utf-8",

        # .env 中出现暂时不认识的配置时，不让程序直接报错
        extra="ignore",
    )

    # 数据库连接地址
    # 如果没有设置 DATABASE_URL，就使用默认 SQLite 地址
    database_url: str = Field(
        default=DEFAULT_DATABASE_URL,

        # 不允许 DATABASE_URL 是空字符串
        min_length=1,
    )


# 创建全局配置对象
# 程序其他文件以后统一从这个对象读取配置
settings = Settings()