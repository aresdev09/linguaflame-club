"""Подключение к базе данных: async engine, фабрика сессий и короткие транзакции.

SQLite настраивается отдельно: включаются WAL, проверка внешних ключей и
длительное ожидание блокировки записи. Без этого параллельные записи падают
с `database is locked`, как только одна транзакция держит блокировку дольше
пяти секунд (значение драйвера по умолчанию).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any, TypeVar

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import get_settings

logger = logging.getLogger(__name__)

# Сколько секунд ждать освобождения блокировки записи SQLite.
SQLITE_BUSY_TIMEOUT_SEC = 30

# Повторы короткой транзакции: busy_timeout не лечит гонку «прочитал — записал»,
# при которой SQLite сразу возвращает database is locked.
WRITE_ATTEMPTS = 5
WRITE_RETRY_BASE_DELAY_SEC = 0.05

T = TypeVar("T")


def is_sqlite(database_url: str) -> bool:
    """True, если адрес ведёт на SQLite."""
    return database_url.startswith("sqlite")


def sqlite_connect_args(database_url: str) -> dict[str, Any]:
    """Параметры соединения драйвера: SQLite получает больший busy timeout."""
    if is_sqlite(database_url):
        return {"timeout": SQLITE_BUSY_TIMEOUT_SEC}
    return {}


def tune_sqlite_for_concurrency(engine: AsyncEngine) -> None:
    """Настраивает каждое соединение SQLite на поведение как в PostgreSQL.

    WAL позволяет читателям не мешать писателю, busy_timeout заставляет
    писателей ждать освобождения блокировки, synchronous=NORMAL ускоряет
    коммит (при падении процесса данные целы), foreign_keys включает
    проверку внешних ключей — так же, как в продакшене на PostgreSQL.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SEC * 1000}")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def build_engine(database_url: str, **kwargs: Any) -> AsyncEngine:
    """Создаёт async-движок с настройками под выбранную базу."""
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=sqlite_connect_args(database_url),
        **kwargs,
    )
    tune_sqlite_for_concurrency(engine)
    return engine


async def transaction_with_retry(
    session_factory: async_sessionmaker[AsyncSession],
    action: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    """Выполняет действие в своей короткой транзакции, повторяя при блокировке.

    Блокировка записи удерживается только до коммита короткой транзакции,
    а неудачная попытка ничего не записала — поэтому её безопасно повторять.
    """
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            async with session_factory() as session:
                async with session.begin():
                    return await action(session)
        except OperationalError as exc:
            if attempt == WRITE_ATTEMPTS or "database is locked" not in str(exc).lower():
                raise
            logger.warning("База занята, повтор записи %d/%d", attempt, WRITE_ATTEMPTS)
            await asyncio.sleep(WRITE_RETRY_BASE_DELAY_SEC * attempt)
    raise AssertionError("цикл повторов завершился без результата")  # pragma: no cover


@lru_cache
def get_engine() -> AsyncEngine:
    """Создаёт (однократно) async-движок по `DATABASE_URL`."""
    return build_engine(get_settings().database_url)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий; объекты не истекают после commit."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)
