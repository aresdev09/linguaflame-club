"""Тесты подключения к базе данных: движок и короткие транзакции."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from bot.database import build_engine, transaction_with_retry


async def test_engine_connects_and_executes() -> None:
    """Движок создаётся и выполняет простой запрос."""
    engine = build_engine("sqlite+aiosqlite://")
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


async def test_transaction_with_retry_commits() -> None:
    """Действие выполняется в своей транзакции и коммитится."""
    # StaticPool: все сессии работают через одно соединение, поэтому
    # SQLite в памяти остаётся одним и тем же между сессиями.
    engine = build_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def action(session: AsyncSession) -> str:
        await session.execute(text("CREATE TABLE demo (id INTEGER PRIMARY KEY, value TEXT)"))
        await session.execute(text("INSERT INTO demo (value) VALUES ('ok')"))
        return "done"

    try:
        assert await transaction_with_retry(factory, action) == "done"

        async with factory() as session:
            rows = (await session.execute(text("SELECT value FROM demo"))).scalars().all()
            assert rows == ["ok"]
    finally:
        await engine.dispose()
