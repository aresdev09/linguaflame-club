"""Окружение Alembic: async-движок и адрес базы из DATABASE_URL."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection

from bot.config import DEFAULT_DATABASE_URL
from bot.database import build_engine
from bot.models import Base

config = context.config

# При запуске миграций из кода бота логирование уже настроено — не перезаписываем.
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name)

# Миграции не требуют токена бота: адрес БД берём из атрибутов запуска,
# затем из DATABASE_URL, затем из .env или значения по умолчанию.
load_dotenv()
database_url = config.attributes.get("database_url") or os.getenv(
    "DATABASE_URL", DEFAULT_DATABASE_URL
)
config.set_main_option("sqlalchemy.url", database_url)

# Схема приложения нужна для 'autogenerate'.
target_metadata = Base.metadata


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


def do_run_migrations(connection: Connection) -> None:
    """Применяет миграции на готовом соединении."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Создаёт движок и применяет миграции на одном соединении.

    Движок общий с ботом (`bot.database.build_engine`): для SQLite
    включаются WAL и проверка внешних ключей — те же настройки,
    что и у рабочего движка бота.
    """
    connectable = build_engine(database_url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
