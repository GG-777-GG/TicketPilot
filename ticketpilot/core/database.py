from collections.abc import Generator


from sqlalchemy import (
    Engine,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    sessionmaker,
)

# 导入默认数据库文件路径和统一配置对象
from ticketpilot.core.config import (
    DEFAULT_DATABASE_FILE,
    settings,
)

DATABASE_URL = settings.database_url

# 为项目中的 SQLAlchemy ORM 模型提供公共基类
class Base(DeclarativeBase):
    pass


# 根据数据库地址创建 SQLAlchemy Engine
def build_engine(database_url: str) -> Engine:
    connect_args: dict[str, bool] = {}

    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(
        database_url,
        connect_args=connect_args,
    )


# 根据 Engine 创建统一配置的 Session 工厂
def build_session_factory(
        database_engine: Engine,
) -> sessionmaker[Session]:
    return sessionmaker(
        bind=database_engine,
        autoflush=False,
        expire_on_commit=False,
    )


# 确保默认 SQLite 数据库所在的 data 文件夹存在
DEFAULT_DATABASE_FILE.parent.mkdir(
    # 如果上级目录不存在，也一起创建
    parents=True,

    # 文件夹已经存在时不报错
    exist_ok=True,
)

# 创建项目全局共用的数据库 Engine
engine = build_engine(DATABASE_URL)

# 创建项目全局共用的 Session 工厂
SessionLocal = build_session_factory(engine)


# 根据已登记的 ORM 模型创建数据库表
def create_tables(
        database_engine: Engine = engine,
) -> None:
    # 导入两个业务模块的 ORM 模型，把它们登记到同一个 Base.metadata。
    import ticketpilot.identity.models
    import ticketpilot.tickets.models

    Base.metadata.create_all(
        bind=database_engine,
    )


# 为一次调用创建并最终关闭数据库 Session
def get_session(
        # Generator 表示函数会通过 yield 提供一个 Session
) -> Generator[Session, None, None]:
    # 调用 Session 工厂，创建一个具体的数据库 Session
    session = SessionLocal()

    # 使用 try 保证 Session 使用期间受到保护
    try:
        # 暂停函数，并把 Session 交给 FastAPI 路由使用
        yield session

    # 无论路由成功还是抛出异常，finally 都会执行
    finally:
        # 关闭 Session，并释放它占用的数据库连接
        session.close()
