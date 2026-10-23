"""Общие фикстуры тестов."""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from bot.database import build_engine
from bot.models import Base


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """SQLite в памяти со свежей схемой для каждого теста.

    Используется рабочий движок из `bot.database`: те же PRAGMA
    (WAL, проверка внешних ключей), что и у приложения.
    StaticPool: все сессии ходят через одно соединение, поэтому
    SQLite в памяти остаётся одним и тем же между сессиями.
    """
    engine = build_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
