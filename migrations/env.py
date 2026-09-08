from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# 导入 ORM 模型，让 TicketRecord 在 Base.metadata 中完成登记
import ticketpilot.db_models

# 导入项目统一使用的数据库地址和 ORM 元数据
from ticketpilot.database import (
    Base,
    DATABASE_URL,
)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
# 优先取得测试代码传入的临时数据库地址
# 如果测试没有传入，就使用项目正式配置的 DATABASE_URL
migration_database_url = config.attributes.get(
    "database_url",
    DATABASE_URL,
)

# 告诉 Alembic 本次迁移应该连接哪个数据库
config.set_main_option(
    "sqlalchemy.url",
    migration_database_url,
)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# 把项目中登记的所有 ORM 表结构交给 Alembic
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
