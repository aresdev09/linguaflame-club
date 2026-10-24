"""Огонёк группы: серия дней активности по часовому поясу чата."""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.repositories import ChatRepo, StreakRepo
from bot.services.streak import StreakService

CHAT_ID = -100500
START = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # понедельник


@pytest_asyncio.fixture(autouse=True)
async def chat_row(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Огонёк принадлежит чату — строка чата нужна из-за внешнего ключа."""
    async with session_factory() as session:
        async with session.begin():
            await ChatRepo(session).get_or_create(CHAT_ID, title="Streak group")


class FakeClock:
    """Управляемое «сейчас» для тестов."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: int = 0, hours: int = 0) -> None:
        self.now = self.now + timedelta(days=days, hours=hours)


async def _mark(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    *,
    timezone: str = "UTC",
) -> int:
    return await StreakService(session_factory, now=clock).mark_active(CHAT_ID, timezone)


async def test_first_activity_starts_streak(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()

    assert await _mark(session_factory, clock) == 1


async def test_same_day_does_not_grow_twice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()

    assert await _mark(session_factory, clock) == 1
    clock.advance(hours=5)
    assert await _mark(session_factory, clock) == 1


async def test_consecutive_days_grow_streak(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()

    assert await _mark(session_factory, clock) == 1
    clock.advance(days=1)
    assert await _mark(session_factory, clock) == 2
    clock.advance(days=1)
    assert await _mark(session_factory, clock) == 3


async def test_skipped_day_resets_streak(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()

    assert await _mark(session_factory, clock) == 1
    clock.advance(days=1)
    assert await _mark(session_factory, clock) == 2
    clock.advance(days=3)
    assert await _mark(session_factory, clock) == 1


async def test_best_streak_survives_reset(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()

    assert await _mark(session_factory, clock) == 1
    clock.advance(days=1)
    assert await _mark(session_factory, clock) == 2
    clock.advance(days=1)
    assert await _mark(session_factory, clock) == 3
    clock.advance(days=10)
    assert await _mark(session_factory, clock) == 1

    async with session_factory() as session:
        streak = await StreakRepo(session).get(CHAT_ID)
    assert streak is not None
    assert streak.current_streak == 1
    assert streak.best_streak == 3


async def test_day_boundary_follows_chat_timezone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """19:00 UTC субботы — уже воскресенье в Самарканде (UTC+5)."""
    clock = FakeClock(now=datetime(2026, 10, 24, 19, 0, tzinfo=UTC))

    first = await _mark(session_factory, clock, timezone="Asia/Samarkand")
    assert first == 1

    # Спустя час (20:00 UTC) там всё ещё то же воскресенье: серия та же.
    clock.advance(hours=1)
    assert await _mark(session_factory, clock, timezone="Asia/Samarkand") == 1

    # Следующие сутки по Самарканду — понедельник, серия растёт.
    clock.advance(days=1)
    assert await _mark(session_factory, clock, timezone="Asia/Samarkand") == 2
