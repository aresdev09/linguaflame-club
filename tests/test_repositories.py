"""Тесты репозиториев: уникальные индексы, миграция chat_id, очки и квоты."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import ScoreEvent
from bot.repositories import (
    ChatRepo,
    ContentRepo,
    GameRepo,
    MemberRepo,
    ScoreRepo,
    StreakRepo,
    UsageRepo,
)

CHAT_ID = -1001
DAY = date(2026, 10, 23)


async def _create_chat(session: AsyncSession, chat_id: int = CHAT_ID) -> None:
    await ChatRepo(session).get_or_create(chat_id, title="Test group")
    await session.commit()


async def test_second_answer_in_round_rejected(session: AsyncSession) -> None:
    """Второй ответ того же участника в раунде отклоняется базой."""
    await _create_chat(session)
    games = GameRepo(session)
    game = await games.create_session(CHAT_ID, kind="quiz", started_by=1)
    await session.commit()
    game_id = game.id

    await games.add_answer(
        game_id, 1, user_id=111, answer="a", is_correct=True, answer_time_ms=1500, points=10
    )
    await session.commit()

    with pytest.raises(IntegrityError):
        await games.add_answer(
            game_id, 1, user_id=111, answer="b", is_correct=False, answer_time_ms=2000, points=0
        )
    await session.rollback()

    # Другой участник в том же раунде и тот же участник в следующем раунде — можно.
    await games.add_answer(
        game_id, 1, user_id=222, answer="c", is_correct=False, answer_time_ms=2500, points=0
    )
    await games.add_answer(
        game_id, 2, user_id=111, answer="d", is_correct=True, answer_time_ms=1000, points=10
    )
    await session.commit()
    assert (await games.get_answer(game_id, 1, 111)).answer == "a"
    assert (await games.get_answer(game_id, 1, 222)).answer == "c"
    assert (await games.get_answer(game_id, 2, 111)).answer == "d"


async def test_only_one_active_game_per_chat(session: AsyncSession) -> None:
    """Вторая активная игра в чате отклоняется, после закрытия — разрешена."""
    await _create_chat(session)
    games = GameRepo(session)

    first = await games.create_session(CHAT_ID, kind="quiz", started_by=1)
    await session.commit()
    first_id = first.id

    with pytest.raises(IntegrityError):
        await games.create_session(CHAT_ID, kind="scramble", started_by=2)
    await session.rollback()

    # В другом чате своя игра — не мешает.
    await _create_chat(session, chat_id=-1002)
    other = await games.create_session(-1002, kind="quiz", started_by=3)
    await session.commit()
    assert other.status == "running"

    # После закрытия первой игры в том же чате можно запускать новую.
    await games.close(first_id, "finished")
    await session.commit()
    second = await games.create_session(CHAT_ID, kind="race", started_by=2)
    await session.commit()
    assert second.status == "running"
    assert (await games.get_active(CHAT_ID)).id == second.id


async def test_migrate_chat_id_moves_all_tables(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Миграция группы в супергруппу переносит chat_id во всех таблицах."""
    old_id, new_id = CHAT_ID, -1000000000001

    async with session_factory() as session:
        await _create_chat(session, old_id)
        await MemberRepo(session).get_or_create(old_id, 111, display_name="Anna")
        await ScoreRepo(session).add_event(old_id, 111, points=10, source="quiz")
        await StreakRepo(session).set_progress(
            old_id, current_streak=3, best_streak=5, last_active_date=DAY
        )
        await ContentRepo(session).create_daily_post(old_id, DAY, "words")
        await ContentRepo(session).mark_content_used(old_id, "hash1", "words")
        await UsageRepo(session).increment(old_id, DAY)
        await GameRepo(session).create_session(old_id, kind="quiz", started_by=111)
        await session.commit()

    async with session_factory() as session:
        await ChatRepo(session).migrate_chat_id(old_id, new_id)
        await session.commit()

    async with session_factory() as session:
        chats = ChatRepo(session)
        assert await chats.get(old_id) is None
        migrated = await chats.get(new_id)
        assert migrated is not None and migrated.title == "Test group"

        assert await MemberRepo(session).get(new_id, 111) is not None
        assert await MemberRepo(session).get(old_id, 111) is None
        assert await ScoreRepo(session).total(new_id, 111) == 10
        streak = await StreakRepo(session).get(new_id)
        assert streak is not None and streak.current_streak == 3
        assert await ContentRepo(session).get_daily_post(new_id, DAY, "words") is not None
        assert await ContentRepo(session).was_content_used(new_id, "hash1")
        assert await UsageRepo(session).get_requests(new_id, DAY) == 1
        assert await GameRepo(session).get_active(new_id) is not None


async def test_daily_post_unique_per_day_and_kind(session: AsyncSession) -> None:
    """Один пост каждого вида в день: дубль отклоняется, другой вид — нет."""
    await _create_chat(session)
    content = ContentRepo(session)

    await content.create_daily_post(CHAT_ID, DAY, "words")
    await session.commit()

    with pytest.raises(IntegrityError):
        await content.create_daily_post(CHAT_ID, DAY, "words")
    await session.rollback()

    await content.create_daily_post(CHAT_ID, DAY, "grammar")
    await session.commit()
    assert await content.get_daily_post(CHAT_ID, DAY, "grammar") is not None


async def test_leaderboard_order_and_opt_out(session: AsyncSession) -> None:
    """Рейтинг: сумма за период, порядок при равенстве, скрытые участники."""
    await _create_chat(session)
    members = MemberRepo(session)
    scores = ScoreRepo(session)
    await members.get_or_create(CHAT_ID, 111, display_name="Anna")
    await members.get_or_create(CHAT_ID, 222, display_name="Bekzod")
    await members.get_or_create(CHAT_ID, 333, display_name="Dilya")
    await scores.add_event(CHAT_ID, 111, points=10, source="quiz")
    await scores.add_event(CHAT_ID, 111, points=5, source="scramble")
    await scores.add_event(CHAT_ID, 222, points=15, source="quiz")
    await scores.add_event(CHAT_ID, 333, points=99, source="quiz")
    await session.commit()

    await members.set_opted_out(CHAT_ID, 333, True)
    await session.commit()

    top = await scores.leaderboard(CHAT_ID)
    # При равенстве выше тот, кто набрал очки раньше (111 отвечал первым).
    assert top == [(111, 15), (222, 15)]
    assert await scores.total(CHAT_ID, 111) == 15
    assert await scores.total(CHAT_ID, 222, until=datetime.now(UTC)) == 15
    assert await scores.total(CHAT_ID, 222, since=datetime.now(UTC)) == 0


async def test_score_event_created_at_is_utc(session: AsyncSession) -> None:
    """Время начислений хранится в UTC."""
    await _create_chat(session)
    before = datetime.now(UTC)
    event = await ScoreRepo(session).add_event(
        CHAT_ID, 111, points=3, source="daily_bonus"
    )
    after = datetime.now(UTC)
    await session.commit()

    # Перечитываем из базы: SQLite не хранит зону, но время должно быть UTC.
    event_id = event.id
    session.expire(event)
    reloaded = await session.get(ScoreEvent, event_id)
    created = reloaded.created_at
    wall_time = created.replace(tzinfo=UTC)
    assert before <= wall_time <= after


async def test_content_cache_reused_for_same_level_and_language(
    session: AsyncSession,
) -> None:
    """Одинаковый уровень и язык получают один и тот же контент из кеша."""
    await _create_chat(session)
    cache = ContentRepo(session)

    stored = await cache.put_cached("quiz_batch", "B1", "ru", {"questions": []})
    await session.commit()

    found = await cache.get_cached("quiz_batch", "B1", "ru")
    assert found is not None and found.id == stored.id
    assert await cache.get_cached("quiz_batch", "B2", "ru") is None


async def test_streak_progress_roundtrip(session: AsyncSession) -> None:
    await _create_chat(session)
    streaks = StreakRepo(session)

    await streaks.set_progress(
        CHAT_ID, current_streak=3, best_streak=5, last_active_date=DAY
    )
    await session.commit()

    streak = await streaks.get(CHAT_ID)
    assert streak is not None
    assert (streak.current_streak, streak.best_streak, streak.last_active_date) == (3, 5, DAY)


async def test_usage_increment_counts_requests(session: AsyncSession) -> None:
    await _create_chat(session)
    usage = UsageRepo(session)

    assert await usage.get_requests(CHAT_ID, DAY) == 0
    assert await usage.increment(CHAT_ID, DAY, tokens=100) == 1
    assert await usage.increment(CHAT_ID, DAY, tokens=50) == 2
    await session.commit()
    assert await usage.get_requests(CHAT_ID, DAY) == 2
