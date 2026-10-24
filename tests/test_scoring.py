"""Тесты очков: журнал, дневной лимит, границы недели, ничьи, opted_out.

Неделя и день считаются по часовому поясу чата, поэтому отдельно
проверяются границы в Asia/Samarkand (UTC+5) и UTC.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import Chat, ScoreEvent
from bot.repositories import (
    ChatRepo,
    GameRepo,
    LeaderboardEntry,
    MemberRepo,
    ScoreRepo,
)
from bot.services.scoring import (
    PERIOD_ALL,
    PERIOD_WEEK,
    MemberStats,
    ScoringService,
    clean_display_name,
    format_me,
    format_top,
    week_start,
)

CHAT_ID = -900


def _settings(**overrides: object) -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None, **overrides)


def _clock(at: datetime):
    return lambda: at


async def _chat(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timezone: str = "UTC",
    chat_id: int = CHAT_ID,
) -> Chat:
    async with session_factory() as session:
        async with session.begin():
            repo = ChatRepo(session)
            chat = await repo.get_or_create(chat_id, title="Test group")
            await repo.update_settings(chat_id, timezone=timezone)
        return chat


async def _member(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    *,
    display_name: str,
    chat_id: int = CHAT_ID,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await MemberRepo(session).get_or_create(
                chat_id, user_id, display_name=display_name
            )


async def _event(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    points: int,
    *,
    source: str = "quiz",
    at: datetime | None = None,
    chat_id: int = CHAT_ID,
) -> None:
    """Записывает событие; `at` позволяет прибить время в тестах границ."""
    async with session_factory() as session:
        async with session.begin():
            event = await ScoreRepo(session).add_event(
                chat_id, user_id, points=points, source=source
            )
            if at is not None:
                event.created_at = at


async def _all_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[ScoreEvent]:
    async with session_factory() as session:
        result = await session.execute(select(ScoreEvent).order_by(ScoreEvent.id))
        return list(result.scalars().all())


# --- 5.1 Начисление очков и дневной лимит ---


async def test_award_writes_event_and_returns_points(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    service = ScoringService(session_factory, _settings())

    awarded = await service.award(CHAT_ID, 111, 7, "daily_bonus")

    assert awarded == 7
    events = await _all_events(session_factory)
    assert [(event.user_id, event.points, event.source) for event in events] == [
        (111, 7, "daily_bonus")
    ]


async def test_award_stops_at_daily_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Дневной лимит обрезает начисление и не пишет пустые события."""
    await _chat(session_factory)
    settings = _settings(score_daily_cap=30)
    service = ScoringService(session_factory, settings)

    assert await service.award(CHAT_ID, 111, 20, "quiz") == 20
    assert await service.award(CHAT_ID, 111, 25, "scramble") == 10
    assert await service.award(CHAT_ID, 111, 5, "gap") == 0

    events = await _all_events(session_factory)
    assert [event.points for event in events] == [20, 10]
    assert sum(event.points for event in events) == 30

    # Лимит у каждого свой: другой участник получает очки полностью.
    assert await service.award(CHAT_ID, 222, 30, "quiz") == 30


async def test_daily_cap_is_counted_in_chat_timezone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сброс лимита происходит в полночь часового пояса чата."""
    await _chat(session_factory, timezone="Asia/Samarkand")
    # Понедельник 20:00 UTC — это уже вторник 01:00 в Самарканде (UTC+5).
    now = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    service = ScoringService(session_factory, _settings(), now=_clock(now))

    # 18:59 UTC = 23:59 понедельника в Самарканде: лимит прошлого дня.
    await _event(
        session_factory, 111, 300, at=datetime(2026, 10, 12, 18, 59, tzinfo=UTC)
    )
    assert await service.award(CHAT_ID, 111, 30, "quiz") == 30

    # 19:00 UTC = 00:00 вторника: это уже текущий локальный день.
    await _event(
        session_factory, 222, 300, at=datetime(2026, 10, 12, 19, 0, tzinfo=UTC)
    )
    assert await service.award(CHAT_ID, 222, 30, "quiz") == 0

    events = await _all_events(session_factory)
    assert [(event.user_id, event.points) for event in events] == [
        (111, 300),
        (111, 30),
        (222, 300),
    ]


async def test_award_ignores_non_positive_points(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    service = ScoringService(session_factory, _settings())

    assert await service.award(CHAT_ID, 111, 0, "quiz") == 0
    assert await service.award(CHAT_ID, 111, -5, "quiz") == 0
    assert await _all_events(session_factory) == []


async def test_award_locked_applies_daily_cap_inside_caller_tx(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Начисление внутри чужой транзакции считает тот же дневной лимит.

    Игры пишут ответ и очки одной транзакцией: лимит должен работать
    и там, без двойных списаний.
    """
    await _chat(session_factory)
    settings = _settings(score_daily_cap=25)
    service = ScoringService(session_factory, settings)

    async with session_factory() as session:
        async with session.begin():
            assert await service.award_locked(session, "UTC", CHAT_ID, 111, 20, "quiz") == 20
            # Видит начисление из той же транзакции.
            assert await service.award_locked(session, "UTC", CHAT_ID, 111, 10, "quiz") == 5

    # Лимит дня исчерпан — обычное начисление ничего не добавляет.
    assert await service.award(CHAT_ID, 111, 10, "quiz") == 0

    events = await _all_events(session_factory)
    assert [event.points for event in events] == [20, 5]


# --- 5.2 Границы недели по часовому поясу чата ---


def test_week_start_in_utc() -> None:
    # Среда 21 октября 2026: неделя началась в понедельник 19-го.
    assert week_start("UTC", datetime(2026, 10, 21, 12, 0, tzinfo=UTC)) == datetime(
        2026, 10, 19, 0, 0, tzinfo=UTC
    )
    # Понедельник 00:00 — граница уже наступила.
    assert week_start("UTC", datetime(2026, 10, 19, 0, 0, tzinfo=UTC)) == datetime(
        2026, 10, 19, 0, 0, tzinfo=UTC
    )
    # Воскресенье 23:59 — ещё прошлая неделя.
    assert week_start("UTC", datetime(2026, 10, 18, 23, 59, tzinfo=UTC)) == datetime(
        2026, 10, 12, 0, 0, tzinfo=UTC
    )


def test_week_start_in_samarkand() -> None:
    """Понедельник 00:00 в Самарканде (UTC+5) — это воскресенье 19:00 UTC."""
    assert week_start(
        "Asia/Samarkand", datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
    ) == datetime(2026, 10, 18, 19, 0, tzinfo=UTC)
    # 18:59 UTC — ещё воскресенье 23:59 локально, неделя прошлая.
    assert week_start(
        "Asia/Samarkand", datetime(2026, 10, 18, 18, 59, tzinfo=UTC)
    ) == datetime(2026, 10, 11, 19, 0, tzinfo=UTC)


async def test_weekly_ranking_respects_samarkand_boundary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Событие в 18:59 UTC — прошлая неделя, в 19:00 UTC — уже новая."""
    chat = await _chat(session_factory, timezone="Asia/Samarkand")
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    service = ScoringService(session_factory, _settings(), now=_clock(now))

    await _event(
        session_factory, 111, 5, at=datetime(2026, 10, 18, 18, 59, tzinfo=UTC)
    )
    await _event(
        session_factory, 111, 7, at=datetime(2026, 10, 18, 19, 0, tzinfo=UTC)
    )
    await _event(
        session_factory, 222, 3, at=datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
    )

    week = await service.top(chat, PERIOD_WEEK)
    assert [(entry.user_id, entry.points) for entry in week] == [(111, 7), (222, 3)]

    all_time = await service.top(chat, PERIOD_ALL)
    assert [(entry.user_id, entry.points) for entry in all_time] == [
        (111, 12),
        (222, 3),
    ]

    stats = await service.member_stats(chat, 111)
    assert stats.week_points == 7
    assert stats.total_points == 12


async def test_weekly_ranking_respects_utc_boundary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Для UTC-группы неделя меняется ровно в понедельник 00:00 UTC."""
    chat = await _chat(session_factory, timezone="UTC")
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    service = ScoringService(session_factory, _settings(), now=_clock(now))

    await _event(
        session_factory, 111, 5, at=datetime(2026, 10, 18, 23, 59, 59, tzinfo=UTC)
    )
    await _event(
        session_factory, 111, 7, at=datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
    )

    week = await service.top(chat, PERIOD_WEEK)
    assert [(entry.user_id, entry.points) for entry in week] == [(111, 7)]

    all_time = await service.top(chat, PERIOD_ALL)
    assert [(entry.user_id, entry.points) for entry in all_time] == [(111, 12)]


# --- 5.2 Суммы, ничьи, скрытые участники ---


async def test_leaderboard_sum_equals_sum_of_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    service = ScoringService(session_factory, _settings())

    inserted = [
        (111, 10),
        (111, 5),
        (222, 15),
        (222, 1),
        (333, 3),
    ]
    for user_id, points in inserted:
        await _event(session_factory, user_id, points)

    top = await service.top(chat, PERIOD_ALL)
    displayed = {entry.user_id: entry.points for entry in top}
    expected = {
        111: 15,
        222: 16,
        333: 3,
    }
    assert displayed == expected
    assert sum(displayed.values()) == sum(points for _, points in inserted)


async def test_tie_is_broken_by_earlier_last_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """При равенстве очков выше тот, у кого последнее событие раньше."""
    chat = await _chat(session_factory)
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    service = ScoringService(session_factory, _settings(), now=_clock(now))

    await _event(
        session_factory, 111, 10, at=datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
    )
    await _event(
        session_factory, 222, 15, at=datetime(2026, 10, 19, 10, 5, tzinfo=UTC)
    )
    # 111 набрал ещё очки позже — его последнее событие теперь позже.
    await _event(
        session_factory, 111, 5, at=datetime(2026, 10, 19, 10, 10, tzinfo=UTC)
    )

    top = await service.top(chat, PERIOD_WEEK)
    assert [(entry.user_id, entry.points) for entry in top] == [(222, 15), (111, 15)]

    assert await _place(session_factory, 222) == 1
    assert await _place(session_factory, 111) == 2


async def test_tie_at_same_timestamp_keeps_insertion_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Часы могут вернуть одно время: порядок задаёт номер события."""
    chat = await _chat(session_factory)
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    service = ScoringService(session_factory, _settings(), now=_clock(now))
    same_time = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)

    await _event(session_factory, 111, 10, at=same_time)
    await _event(session_factory, 222, 10, at=same_time)

    top = await service.top(chat, PERIOD_WEEK)
    assert [entry.user_id for entry in top] == [111, 222]


async def test_opted_out_member_hidden_from_rating(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    await _member(session_factory, 111, display_name="Anna")
    await _member(session_factory, 222, display_name="Bekzod")
    await _event(
        session_factory, 111, 10, at=datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    )
    await _event(
        session_factory, 222, 20, at=datetime(2026, 10, 20, 11, 0, tzinfo=UTC)
    )
    async with session_factory() as session:
        async with session.begin():
            await MemberRepo(session).set_opted_out(CHAT_ID, 222, True)

    service = ScoringService(session_factory, _settings())
    top = await service.top(await _chat(session_factory), PERIOD_ALL)
    assert [(entry.user_id, entry.points) for entry in top] == [(111, 10)]

    assert await _place(session_factory, 111) == 1
    # Скрытый участник не получает место, но журнал не удаляется.
    assert await _place(session_factory, 222) is None
    async with session_factory() as session:
        assert await ScoreRepo(session).total(CHAT_ID, 222) == 20


async def test_member_stats_counts_answers_and_best_game(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    async with session_factory() as session:
        async with session.begin():
            game = await GameRepo(session).create_session(
                CHAT_ID, kind="quiz", started_by=111
            )
            answers = GameRepo(session)
            await answers.add_answer(
                game.id, 1, user_id=111, answer="a", is_correct=True,
                answer_time_ms=1000, points=10,
            )
            await answers.add_answer(
                game.id, 2, user_id=111, answer="b", is_correct=False,
                answer_time_ms=2000, points=0,
            )
            await answers.add_answer(
                game.id, 3, user_id=111, answer="c", is_correct=True,
                answer_time_ms=1500, points=10,
            )
    await _event(session_factory, 111, 10, source="quiz")
    await _event(session_factory, 111, 5, source="scramble")

    service = ScoringService(session_factory, _settings())
    stats = await service.member_stats(chat, 111)

    assert stats.answers == 3
    assert stats.correct_answers == 2
    assert stats.accuracy == 67
    assert stats.best_game == "quiz"
    assert stats.total_points == 15


async def _place(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> int | None:
    async with session_factory() as session:
        return await ScoreRepo(session).place(
            CHAT_ID, user_id, since=datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        )


# --- 5.5 Форматирование: экранирование, без @, обрезка ---


def _entry(user_id: int, points: int, name: str | None) -> LeaderboardEntry:
    return LeaderboardEntry(
        user_id=user_id,
        points=points,
        display_name=name,
        last_event_at=datetime(2026, 10, 19, 10, 0, tzinfo=UTC),
    )


def test_clean_display_name_removes_mentions_and_escapes() -> None:
    cleaned = clean_display_name("<b>Bob</b> @nobody", 5, "en")
    assert cleaned == "&lt;b&gt;Bob&lt;/b&gt; nobody"
    assert "@" not in cleaned


def test_clean_display_name_truncates() -> None:
    cleaned = clean_display_name("A" * 30, 5, "en")
    assert len(cleaned) == 20
    assert cleaned.endswith("…")


def test_clean_display_name_falls_back_to_player_id() -> None:
    assert clean_display_name(None, 42, "en") == "Player 42"


def test_format_top_adds_medals_and_hides_mentions() -> None:
    entries = [
        _entry(1, 120, "Anna"),
        _entry(2, 100, "@bek"),
        _entry(3, 90, "Dilya"),
        _entry(4, 50, "Egor"),
    ]

    text = format_top(entries, "en", PERIOD_ALL)

    assert "🏆 Leaderboard — all time" in text
    assert "🥇 Anna — 120 pts" in text
    assert "🥈 bek — 100 pts" in text
    assert "🥉 Dilya — 90 pts" in text
    assert "4. Egor — 50 pts" in text
    assert "@" not in text


def test_format_top_empty_shows_hint() -> None:
    text = format_top([], "en", PERIOD_WEEK)
    assert "No scores yet. Start with /quiz" in text


def test_format_me_shows_points_place_and_accuracy() -> None:
    stats = MemberStats(
        week_points=45,
        total_points=210,
        place=3,
        answers=12,
        correct_answers=10,
        best_game="quiz",
    )
    text = format_me(stats, "en")

    assert "📊 Your stats" in text
    assert "Points this week: 45" in text
    assert "Points all time: 210" in text
    assert "Place: #3" in text
    assert "Answers: 12 (83% correct)" in text
    assert "Best game: Quiz" in text


def test_format_me_without_place_or_game() -> None:
    stats = MemberStats(
        week_points=0,
        total_points=10,
        place=None,
        answers=0,
        correct_answers=0,
        best_game=None,
    )
    text = format_me(stats, "ru")

    assert "Место: —" in text
    assert "Ответы: 0 (0% верных)" in text
    assert "Лучшая игра" not in text
