"""Огонёк группы: серия дней активности по часовому поясу чата.

Главные правила: день считается активным, если кто-то заработал очки;
в один день серия не растёт дважды; пропущенный день сбрасывает серию,
а рекорд остаётся.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.types import Chat, Message, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.repositories import ChatRepo, StreakRepo
from bot.routers.leaderboard import show_streak
from bot.services.streak import (
    MILESTONES,
    StreakService,
    StreakState,
    format_streak,
    is_milestone,
)

CHAT_ID = -100500
START = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # понедельник
GROUP = Chat(id=CHAT_ID, type="group", title="Streak group")
ANNA = User(id=55, is_bot=False, first_name="Anna")


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
    return await StreakService(session_factory, now=clock).register_activity(
        CHAT_ID, timezone
    )


async def _streak_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    """Серия и рекорд так, как они записаны в базе."""
    async with session_factory() as session:
        streak = await StreakRepo(session).get(CHAT_ID)
    assert streak is not None
    return streak.current_streak, streak.best_streak


def _message(text: str = "/streak") -> Message:
    return Message(
        message_id=300,
        date=datetime.now(UTC),
        chat=GROUP,
        from_user=ANNA,
        text=text,
    )


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


async def test_streak_grows_right_after_local_midnight(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Переход суток: 23:59 и 01:59 по Самарканду — разные дни."""
    clock = FakeClock(now=datetime(2026, 10, 24, 18, 59, tzinfo=UTC))

    assert await _mark(session_factory, clock, timezone="Asia/Samarkand") == 1
    clock.advance(hours=2)  # 20:59 UTC — воскресенье, но уже новые сутки
    assert await _mark(session_factory, clock, timezone="Asia/Samarkand") == 2


async def test_state_marks_activity_today(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    await _mark(session_factory, clock)
    clock.advance(hours=3)

    state = await StreakService(session_factory, now=clock).state(CHAT_ID, "UTC")

    assert state.current == 1
    assert state.active_today is True
    assert state.at_risk is False


async def test_state_keeps_streak_alive_until_day_ends(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    await _mark(session_factory, clock)
    clock.advance(days=1)

    state = await StreakService(session_factory, now=clock).state(CHAT_ID, "UTC")

    assert state.current == 1
    assert state.active_today is False
    assert state.at_risk is True


async def test_state_shows_broken_streak_as_zero(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    for _ in range(3):
        await _mark(session_factory, clock)
        clock.advance(days=1)
    clock.advance(days=2)

    state = await StreakService(session_factory, now=clock).state(CHAT_ID, "UTC")

    assert state.current == 0
    assert state.best == 3
    assert state.active_today is False


async def test_state_without_activity_is_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    state = await StreakService(session_factory, now=FakeClock()).state(CHAT_ID, "UTC")

    assert state.current == 0
    assert state.best == 0
    assert state.last_active_date is None


async def test_reset_broken_zeroes_streak_and_reports_loss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    for _ in range(4):
        await _mark(session_factory, clock)
        clock.advance(days=1)
    clock.advance(days=1)  # день пропущен

    service = StreakService(session_factory, now=clock)
    assert await service.reset_broken(CHAT_ID, "UTC") == 4
    assert await _streak_row(session_factory) == (0, 4)
    # Повторный вызов ничего не теряет: сообщение о потере только одно.
    assert await service.reset_broken(CHAT_ID, "UTC") == 0


async def test_reset_broken_keeps_streak_alive_today(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    await _mark(session_factory, clock)
    clock.advance(days=1)
    await _mark(session_factory, clock)

    service = StreakService(session_factory, now=clock)
    assert await service.reset_broken(CHAT_ID, "UTC") == 0
    assert await _streak_row(session_factory) == (2, 2)


def test_milestones_are_known_days() -> None:
    assert MILESTONES == (3, 7, 14, 30, 100)
    assert is_milestone(7) is True
    assert is_milestone(8) is False


async def test_best_streak_reads_record(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    for _ in range(3):
        await _mark(session_factory, clock)
        clock.advance(days=1)

    assert await StreakService(session_factory, now=clock).best_streak(CHAT_ID) == 3


def test_format_streak_shows_current_and_best() -> None:
    state = StreakState(
        current=3, best=12, last_active_date=date(2026, 10, 25), active_today=True
    )

    text = format_streak(state, "en")

    assert "Current streak: 3 days" in text
    assert "Best streak: 12 days" in text
    assert "already earned points" in text


def test_format_streak_uses_russian_plural_forms() -> None:
    assert "1 день" in format_streak(StreakState(1, 1, None, True), "ru")
    assert "2 дня" in format_streak(StreakState(2, 2, None, True), "ru")
    assert "5 дней" in format_streak(StreakState(5, 5, None, True), "ru")


def test_format_streak_when_flame_is_out() -> None:
    state = StreakState(
        current=0, best=9, last_active_date=None, active_today=False
    )

    text = format_streak(state, "en")

    assert "The flame is out" in text
    assert "Best streak: 9 days" in text
    assert "no points yet" in text


async def test_streak_command_replies_with_flame(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    await _mark(session_factory, clock)
    sender = AsyncMock()

    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
    assert chat is not None

    await show_streak(
        message=_message(),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        sender=sender,
    )

    text = sender.reply.await_args.args[1]
    assert "🔥 Group flame" in text
