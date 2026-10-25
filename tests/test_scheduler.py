"""Тесты планировщика: пост дня, огонёк, напоминания, лимиты и ошибки.

Главные правила этапа: автосообщение уходит по локальному времени
группы ровно один раз, в сутки их не больше двух, в тихие часы бот
молчит, заброшенные группы не получают спама, а ошибки Telegram не
роняют планировщик и правильно выключают чат.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import Chat as TelegramChat
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import Chat as ChatRow
from bot.models import DailyPost, ScheduledMessage, ScoreEvent, utcnow
from bot.repositories import (
    ChatRepo,
    ContentRepo,
    GameRepo,
    MemberRepo,
    SchedulerRepo,
    ScoreRepo,
    StreakRepo,
)
from bot.sender import Sender
from bot.services.ai_errors import AIRequestError
from bot.services.content import ContentService
from bot.services.daily import DailyService
from bot.services.fake_ai_client import FakeAIClient
from bot.services.scheduler import (
    JOURNAL_DAILY,
    JOURNAL_MILESTONE,
    JOURNAL_RECAP,
    JOURNAL_REMINDER,
    JOURNAL_STREAK_LOST,
    GroupScheduler,
)
from bot.services.scoring import ScoringService
from bot.services.streak import StreakService

CHAT_ID = -700
OTHER_CHAT_ID = -701
SAMARKAND = "Asia/Samarkand"

# Понедельник, 09:00 по UTC.
MONDAY_MORNING = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


class FakeClock:
    """Управляемое «сейчас» планировщика."""

    def __init__(self, now: datetime = MONDAY_MORNING) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now = self.now + timedelta(**kwargs)


class RecordingSender:
    """Отправка без Telegram: запоминает сообщения и умеет падать."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.markups: list[Any] = []
        self.error = error

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        reply_markup: Any = None,
        reply_to_message_id: int | None = None,
    ) -> Message:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text))
        self.markups.append(reply_markup)
        return Message(
            message_id=len(self.sent),
            date=utcnow(),
            chat=TelegramChat(id=chat_id, type="group"),
            text=text,
        )

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


def _settings(**overrides: object) -> Settings:
    """Настройки планировщика: без разброса и с коротким ожиданием повтора."""
    defaults: dict[str, object] = {
        "scheduler_jitter_max_seconds": 0,
        "scheduler_retry_delay_seconds": 60,
        "scheduler_tick_seconds": 60,
    }
    defaults.update(overrides)
    return Settings(bot_token="123:TEST", _env_file=None, **defaults)


def _scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    clock: FakeClock,
    sender: Any,
    settings: Settings | None = None,
    client: FakeAIClient | None = None,
) -> GroupScheduler:
    """Собирает планировщик с управляемыми часами и заглушкой ИИ."""
    settings = settings or _settings()
    content = ContentService(session_factory, client or FakeAIClient(), settings)
    streak = StreakService(session_factory, now=clock)
    scoring = ScoringService(session_factory, settings, now=clock, streak=streak)
    daily = DailyService(
        session_factory=session_factory,
        settings=settings,
        content=content,
        scoring=scoring,
        streak=streak,
        now=clock,
    )
    return GroupScheduler(
        session_factory=session_factory,
        settings=settings,
        sender=sender,
        daily=daily,
        scoring=scoring,
        streak=streak,
        now=clock,
    )


async def _chat(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int = CHAT_ID,
    *,
    timezone: str = "UTC",
    daily_time: time = time(9, 0),
    words: bool = True,
    grammar: bool = False,
    words_count: int = 2,
    quiet: tuple[time, time] = (time(22, 0), time(7, 0)),
    active: bool = True,
    created_at: datetime | None = None,
) -> ChatRow:
    """Создаёт группу с нужными настройками поста дня."""
    async with session_factory() as session:
        async with session.begin():
            repo = ChatRepo(session)
            await repo.get_or_create(chat_id, title="Study group")
            await repo.update_settings(
                chat_id,
                timezone=timezone,
                daily_time=daily_time,
                daily_words=words,
                daily_grammar=grammar,
                words_count=words_count,
                quiet_from=quiet[0],
                quiet_to=quiet[1],
                active=active,
                created_at=created_at or utcnow(),
            )
    async with session_factory() as session:
        chat = await ChatRepo(session).get(chat_id)
    assert chat is not None
    return chat


async def _seed_streak(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    days: int,
    last_active: date,
    best: int | None = None,
    chat_id: int = CHAT_ID,
) -> None:
    """Записывает готовый огонёк: серия, рекорд и последний активный день."""
    async with session_factory() as session:
        async with session.begin():
            await StreakRepo(session).set_progress(
                chat_id,
                current_streak=days,
                best_streak=days if best is None else best,
                last_active_date=last_active,
            )


async def _seed_scores(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    points: int,
    *,
    at: datetime,
    name: str = "Anna",
) -> None:
    """Начисление очков с нужной датой: так собираются итоги недели."""
    async with session_factory() as session:
        async with session.begin():
            await MemberRepo(session).get_or_create(
                CHAT_ID, user_id, display_name=name
            )
            event = await ScoreRepo(session).add_event(
                CHAT_ID, user_id, points=points, source="quiz"
            )
            event.created_at = at


async def _seed_answers(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    correct: int,
    wrong: int,
    name: str,
    days_ago: int = 5,
) -> None:
    """Ответы участника в игре на прошлой неделе."""
    at = datetime(2026, 10, 19, 9, 0, tzinfo=UTC) - timedelta(days=days_ago)
    async with session_factory() as session:
        async with session.begin():
            await MemberRepo(session).get_or_create(CHAT_ID, user_id, display_name=name)
            repo = GameRepo(session)
            game = await repo.create_session(CHAT_ID, kind="quiz", started_by=user_id)
            for index in range(correct + wrong):
                answer = await repo.add_answer(
                    game.id,
                    round=index + 1,
                    user_id=user_id,
                    answer="a",
                    is_correct=index < correct,
                    answer_time_ms=1000,
                    points=10 if index < correct else 0,
                )
                answer.created_at = at
            # Одна игра статуса `running` на чат: закрываем, чтобы открыть следующую.
            await repo.close(game.id, "finished")


async def _seed_bank_words(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    count: int = 2,
    level: str = "B1",
    language: str = "en",
) -> None:
    """Слова для запасного поста дня: банк заполняется без ИИ."""
    async with session_factory() as session:
        async with session.begin():
            repo = ContentRepo(session)
            for index in range(count):
                await repo.add_bank_item(
                    "words",
                    level,
                    language,
                    {
                        "word": f"bankword{index}",
                        "transcription": f"/b{index}/",
                        "translation": f"translation {index}",
                        "example": f"Example with bankword{index}.",
                    },
                )


async def _seed_journal(
    session_factory: async_sessionmaker[AsyncSession],
    kinds: list[str],
    on_date: date,
) -> None:
    """Уже отправленные автосообщения дня: так проверяется лимит в сутки."""
    async with session_factory() as session:
        async with session.begin():
            repo = SchedulerRepo(session)
            for kind in kinds:
                row = await repo.create(CHAT_ID, on_date, kind)
                await repo.mark_sent(row.id, 1)


async def _daily_posts(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[DailyPost]:
    async with session_factory() as session:
        result = await session.execute(select(DailyPost))
        return list(result.scalars().all())


async def _journal(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[ScheduledMessage]:
    async with session_factory() as session:
        result = await session.execute(select(ScheduledMessage))
        return list(result.scalars().all())


async def _chat_active(
    session_factory: async_sessionmaker[AsyncSession], chat_id: int = CHAT_ID
) -> bool:
    async with session_factory() as session:
        chat = await ChatRepo(session).get(chat_id)
    assert chat is not None
    return chat.active


# --- время поста в разных часовых поясах ---


async def test_scheduler_start_and_shutdown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """start() включает задачу tick, shutdown() её выключает."""
    scheduler = _scheduler(
        session_factory, clock=FakeClock(), sender=RecordingSender()
    )

    assert scheduler.running is False
    scheduler.start()
    try:
        assert scheduler.running is True
    finally:
        scheduler.shutdown()
    assert scheduler.running is False


async def test_post_goes_out_at_local_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """09:00 по UTC: до этого часа бот молчит, после — публикует."""
    clock = FakeClock(datetime(2026, 10, 19, 8, 59, tzinfo=UTC))
    await _chat(session_factory)
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    assert sender.sent == []

    clock.advance(minutes=1)
    await scheduler.tick()

    assert len(sender.sent) == 1
    assert "Words of the day" in sender.texts[0]
    assert sender.sent[0][0] == CHAT_ID


async def test_post_waits_for_local_time_in_samarkand(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Asia/Samarkand (UTC+5): 04:00 UTC — это 09:00 местного времени."""
    clock = FakeClock(datetime(2026, 10, 21, 3, 59, tzinfo=UTC))
    await _chat(session_factory, timezone=SAMARKAND)
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    assert sender.sent == []

    clock.advance(minutes=1)
    await scheduler.tick()

    assert len(sender.sent) == 1
    posts = await _daily_posts(session_factory)
    assert len(posts) == 1
    assert posts[0].date == date(2026, 10, 21)
    assert posts[0].posted_at is not None


async def test_post_for_utc_chat_does_not_follow_samarkand(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """В 04:00 UTC у группы с поясом UTC ещё ночь — поста нет."""
    clock = FakeClock(datetime(2026, 10, 21, 4, 0, tzinfo=UTC))
    await _chat(session_factory, timezone="UTC")
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []


async def test_post_crosses_local_midnight(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Переход суток: 00:30 по Самарканду — это 19:30 UTC прошлого дня."""
    clock = FakeClock(datetime(2026, 10, 21, 19, 25, tzinfo=UTC))
    await _chat(
        session_factory,
        timezone=SAMARKAND,
        daily_time=time(0, 30),
        quiet=(time(0, 0), time(0, 0)),
    )
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    assert sender.sent == []

    clock.advance(minutes=5)  # 19:30 UTC = 00:30 нового дня в Самарканде
    await scheduler.tick()

    assert len(sender.sent) == 1
    posts = await _daily_posts(session_factory)
    assert posts[0].date == date(2026, 10, 22)


async def test_repeat_tick_does_not_duplicate_post(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Повторный tick в ту же минуту и позже дубль не создаёт."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory)
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    await scheduler.tick()
    clock.advance(hours=3)
    await scheduler.tick()

    assert len(sender.sent) == 1
    assert len(await _daily_posts(session_factory)) == 1
    journal = await _journal(session_factory)
    assert [row.kind for row in journal] == [JOURNAL_DAILY]


async def test_word_post_has_got_it_button(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Под постом дня — кнопка «Got it», привязанная к записи поста."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory)
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    markup = sender.markups[0]
    assert markup is not None
    callback = markup.inline_keyboard[0][0].callback_data
    assert callback.startswith("daily:got:")
    posts = await _daily_posts(session_factory)
    assert callback.endswith(str(posts[0].id))


async def test_no_post_when_nothing_selected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Слова и грамматика выключены — планировщик молчит."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory, words=False, grammar=False)
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []


async def test_inactive_chat_is_skipped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Бота удалили — автосообщения в этот чат не идут."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory, active=False)
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []


# --- тихие часы и разброс отправки ---


async def test_quiet_hours_move_post_to_first_allowed_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Время поста внутри тихих часов переносится на их конец."""
    clock = FakeClock(datetime(2026, 10, 19, 22, 30, tzinfo=UTC))
    await _chat(session_factory, daily_time=time(23, 0))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    assert sender.sent == []  # 23:00 ещё не наступило, а сейчас тихие часы

    clock.advance(hours=8, minutes=30)  # 07:00 следующего дня
    await scheduler.tick()

    assert len(sender.sent) == 1


async def test_quiet_hours_can_be_disabled(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Выключенные тихие часы ничего не переносят."""
    clock = FakeClock(datetime(2026, 10, 19, 23, 0, tzinfo=UTC))
    await _chat(
        session_factory,
        daily_time=time(23, 0),
        quiet=(time(0, 0), time(0, 0)),
    )
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert len(sender.sent) == 1


async def test_jitter_delays_post_deterministically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Разброс стабилен для группы и дня: пост ждёт свой сдвиг."""
    await _chat(session_factory)
    clock = FakeClock(MONDAY_MORNING)
    sender = RecordingSender()
    scheduler = _scheduler(
        session_factory,
        clock=clock,
        sender=sender,
        settings=_settings(scheduler_jitter_max_seconds=120),
    )
    jitter = scheduler._jitter_seconds(CHAT_ID, date(2026, 10, 19), JOURNAL_DAILY)
    assert 0 <= jitter <= 120
    assert jitter == scheduler._jitter_seconds(CHAT_ID, date(2026, 10, 19), JOURNAL_DAILY)

    clock.now = MONDAY_MORNING + timedelta(seconds=jitter - 1)
    await scheduler.tick()
    if jitter > 0:
        assert sender.sent == []

    clock.now = MONDAY_MORNING + timedelta(seconds=jitter)
    await scheduler.tick()
    assert len(sender.sent) == 1


# --- огонёк: напоминания, потеря и вехи ---


async def test_evening_reminder_when_no_activity_today(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """20:00 по местному времени: огонёк под угрозой — бот напоминает."""
    clock = FakeClock(datetime(2026, 10, 19, 14, 59, tzinfo=UTC))
    await _chat(
        session_factory,
        timezone=SAMARKAND,
        words=False,
        daily_time=time(9, 0),
    )
    await _seed_streak(session_factory, days=5, last_active=date(2026, 10, 18))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    assert sender.sent == []

    clock.advance(minutes=1)  # 15:00 UTC = 20:00 в Самарканде
    await scheduler.tick()

    assert len(sender.sent) == 1
    assert "flame is at risk" in sender.texts[0]
    assert "5 days" in sender.texts[0]


async def test_evening_reminder_is_sent_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock(datetime(2026, 10, 19, 20, 0, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_streak(session_factory, days=2, last_active=date(2026, 10, 18))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    await scheduler.tick()

    assert len(sender.sent) == 1


async def test_no_reminder_after_todays_activity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сегодня очки уже есть — напоминание не нужно."""
    clock = FakeClock(datetime(2026, 10, 19, 20, 0, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_streak(session_factory, days=4, last_active=date(2026, 10, 19))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []


async def test_no_reminder_when_flame_is_out(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Огонёк погас и сообщение об этом уже ушло — напоминать не о чем."""
    clock = FakeClock(datetime(2026, 10, 19, 20, 0, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_journal(session_factory, [JOURNAL_STREAK_LOST], date(2026, 10, 19))
    await _seed_streak(session_factory, days=3, last_active=date(2026, 10, 15))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []


async def test_streak_lost_message_in_the_morning(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Пропущенный день: одно утреннее сообщение и сброс серии."""
    clock = FakeClock(datetime(2026, 10, 19, 8, 59, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_streak(session_factory, days=4, last_active=date(2026, 10, 17))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    assert sender.sent == []  # утро ещё не наступило

    clock.advance(minutes=1)
    await scheduler.tick()
    await scheduler.tick()

    assert len(sender.sent) == 1
    assert "flame went out after 4 days" in sender.texts[0]
    async with session_factory() as session:
        streak = await StreakRepo(session).get(CHAT_ID)
    assert streak is not None
    assert streak.current_streak == 0
    assert streak.best_streak == 4


async def test_milestone_is_celebrated_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Три дня подряд: группа получает поздравление один раз."""
    clock = FakeClock(datetime(2026, 10, 19, 12, 0, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_streak(session_factory, days=3, last_active=date(2026, 10, 19))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    await scheduler.tick()

    assert len(sender.sent) == 1
    assert "3 days in a row" in sender.texts[0]


async def test_milestone_only_on_milestone_days(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock(datetime(2026, 10, 19, 12, 0, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_streak(session_factory, days=4, last_active=date(2026, 10, 19))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []


# --- лимиты и заброшенные группы ---


async def test_post_and_milestone_fit_in_two_messages(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Пост дня и веха огонька уходят в один проход — это уже два сообщения."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory, daily_time=time(9, 0))
    await _seed_streak(session_factory, days=7, last_active=date(2026, 10, 19))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    clock.advance(hours=11)  # вечером напоминания не будет: очки уже есть
    await scheduler.tick()

    assert len(sender.sent) == 2
    assert "Words of the day" in sender.texts[0]
    assert "7 days in a row" in sender.texts[1]
    journal = await _journal(session_factory)
    assert sorted(row.kind for row in journal) == [JOURNAL_DAILY, JOURNAL_MILESTONE]


async def test_third_auto_message_is_blocked_by_daily_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Больше двух автосообщений в сутки не уходит даже с вехой."""
    clock = FakeClock(datetime(2026, 10, 19, 12, 0, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_journal(
        session_factory, [JOURNAL_DAILY, JOURNAL_REMINDER], date(2026, 10, 19)
    )
    await _seed_streak(session_factory, days=3, last_active=date(2026, 10, 19))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []
    assert len(await _journal(session_factory)) == 2


async def test_no_auto_messages_after_week_without_activity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Через 7 дней без активности автосообщения прекращаются."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory, created_at=utcnow() - timedelta(days=30))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    assert sender.sent == []

    # Группа снова ожила: очки заработаны — автосообщения возвращаются.
    await _seed_streak(session_factory, days=1, last_active=date(2026, 10, 19))
    clock.advance(days=1)
    await scheduler.tick()

    assert len(sender.sent) == 1


async def test_unplayed_group_still_gets_post_within_week(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Новая группа без очков: первые дни пост дня ещё приходит."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory, created_at=utcnow() - timedelta(days=2))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert len(sender.sent) == 1


# --- итоги недели ---


async def test_recap_on_monday_after_post_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Понедельник: итоги прошлой недели публикуются после поста дня."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 29, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_scores(
        session_factory, 55, 40, at=datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    )
    await _seed_scores(
        session_factory, 66, 10, at=datetime(2026, 10, 15, 12, 0, tzinfo=UTC), name="Bob"
    )
    await _seed_streak(session_factory, days=6, last_active=date(2026, 10, 18), best=9)
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()
    assert sender.sent == []  # до 09:30 итогов нет

    clock.advance(minutes=1)
    await scheduler.tick()
    await scheduler.tick()

    assert len(sender.sent) == 1
    text = sender.texts[0]
    assert "Week in review" in text
    assert "Winner of the week: Anna — 40 pts" in text
    assert "Bob — 10 pts" in text
    assert "Flame record: 9 days" in text
    journal = await _journal(session_factory)
    assert [row.kind for row in journal] == [JOURNAL_RECAP]


async def test_recap_does_not_change_scores(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Победитель получает только звание: очки не меняются."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 30, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_scores(
        session_factory, 55, 40, at=datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    )
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    async with session_factory() as session:
        total = await session.execute(select(func.coalesce(func.sum(ScoreEvent.points), 0)))
    assert int(total.scalar_one()) == 40


async def test_recap_skipped_without_points_last_week(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Неделя прошла без очков — итогов нет."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 30, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_scores(
        session_factory, 55, 40, at=datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    )
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []


async def test_recap_only_on_monday(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock(datetime(2026, 10, 20, 9, 30, tzinfo=UTC))  # вторник
    await _chat(session_factory, words=False)
    await _seed_scores(
        session_factory, 55, 40, at=datetime(2026, 10, 19, 7, 0, tzinfo=UTC)
    )
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert sender.sent == []


async def test_recap_shows_best_accuracy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Лучший по точности недели попадает в итоги."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 30, tzinfo=UTC))
    await _chat(session_factory, words=False)
    await _seed_scores(
        session_factory, 55, 40, at=datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    )
    await _seed_answers(session_factory, user_id=55, correct=5, wrong=1, name="Anna")
    await _seed_answers(session_factory, user_id=66, correct=4, wrong=4, name="Bob")
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert "Best accuracy: Anna — 83% (6 answers)" in sender.texts[0]


# --- сдвиг времени для ручной проверки ---


async def test_time_offset_shifts_scheduler_decisions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """SCHEDULER_TIME_OFFSET переносит «сейчас» планировщика вперёд."""
    clock = FakeClock(datetime(2026, 10, 19, 6, 0, tzinfo=UTC))
    await _chat(session_factory, daily_time=time(9, 0))
    sender = RecordingSender()
    scheduler = _scheduler(
        session_factory,
        clock=clock,
        sender=sender,
        settings=_settings(scheduler_time_offset="3h"),
    )

    assert scheduler.now() == datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    await scheduler.tick()

    assert len(sender.sent) == 1


async def test_negative_time_offset_waits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Отрицательный сдвиг откладывает пост: 09:00 минус час — это 08:00."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 30, tzinfo=UTC))
    await _chat(session_factory, daily_time=time(9, 0))
    sender = RecordingSender()
    scheduler = _scheduler(
        session_factory,
        clock=clock,
        sender=sender,
        settings=_settings(scheduler_time_offset="-1h"),
    )

    await scheduler.tick()

    assert sender.sent == []


# --- ошибки и недоступные чаты ---


async def test_chat_is_deactivated_when_bot_is_kicked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sender сообщает, что бота удалили — чат выключается."""
    from bot.sender import ChatUnavailable

    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory)
    sender = RecordingSender(error=ChatUnavailable("bot was kicked from the group"))
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert await _chat_active(session_factory) is False


async def test_forbidden_error_deactivates_chat_through_sender(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TelegramForbiddenError от Telegram: чат помечается неактивным."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory)
    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramForbiddenError(
            method=AsyncMock(), message="Forbidden: bot was kicked from the group chat"
        )
    )
    sender = Sender(bot, per_chat_limit=100, global_limit=100)
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert await _chat_active(session_factory) is False


async def test_retry_after_delays_post_without_deactivation(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TelegramRetryAfter: пост переносится, чат остаётся активным."""
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("bot.sender.asyncio.sleep", fake_sleep)

    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory)
    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramRetryAfter(
            method=AsyncMock(), message="Too Many Requests", retry_after=3
        )
    )
    sender = Sender(bot, per_chat_limit=100, global_limit=100)
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert await _chat_active(session_factory) is True
    journal = await _journal(session_factory)
    assert journal[0].posted_at is None
    assert journal[0].next_attempt_at is not None

    # Telegram снова доступен: спустя время пост уходит.
    bot.send_message = AsyncMock(return_value=AsyncMock(message_id=11))
    clock.advance(minutes=2)
    await scheduler.tick()

    journal = await _journal(session_factory)
    assert journal[0].posted_at is not None
    assert journal[0].message_id == 11


async def test_retry_after_is_repeated_inside_sender(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Один RetryAfter не мешает посту: Sender повторяет отправку сам."""
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("bot.sender.asyncio.sleep", fake_sleep)

    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory)
    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=[
            TelegramRetryAfter(
                method=AsyncMock(), message="Too Many Requests", retry_after=5
            ),
            AsyncMock(message_id=12),
        ]
    )
    sender = Sender(bot, per_chat_limit=100, global_limit=100)
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    journal = await _journal(session_factory)
    assert journal[0].posted_at is not None
    assert journal[0].message_id == 12


async def test_content_failure_retries_then_uses_bank(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Три попытки собрать контент, затем запасной пост из банка."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory, words_count=2)
    await _seed_bank_words(session_factory)
    sender = RecordingSender()
    scheduler = _scheduler(
        session_factory,
        clock=clock,
        sender=sender,
        settings=_settings(scheduler_content_attempts=2, scheduler_retry_delay_seconds=60),
        client=FakeAIClient(error=AIRequestError("ИИ недоступен")),
    )

    await scheduler.tick()
    assert sender.sent == []
    journal = await _journal(session_factory)
    assert journal[0].attempts == 1
    assert journal[0].posted_at is None

    # Повтор раньше назначенного времени не делается.
    clock.advance(seconds=30)
    await scheduler.tick()
    journal = await _journal(session_factory)
    assert journal[0].attempts == 1

    clock.advance(seconds=30)  # вторая попытка закончилась неудачей
    await scheduler.tick()
    journal = await _journal(session_factory)
    assert journal[0].attempts == 2

    clock.advance(seconds=60)  # попытки кончились — запасной контент
    await scheduler.tick()
    assert len(sender.sent) == 1
    assert "bankword" in sender.texts[0]
    journal = await _journal(session_factory)
    assert journal[0].posted_at is not None


async def test_content_failure_without_bank_skips_the_day(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Запасного контента нет — день пропускается без ошибок."""
    clock = FakeClock(datetime(2026, 10, 19, 9, 0, tzinfo=UTC))
    await _chat(session_factory)
    sender = RecordingSender()
    scheduler = _scheduler(
        session_factory,
        clock=clock,
        sender=sender,
        settings=_settings(scheduler_content_attempts=1),
        client=FakeAIClient(error=AIRequestError("ИИ недоступен")),
    )

    await scheduler.tick()
    clock.advance(minutes=2)
    await scheduler.tick()

    assert sender.sent == []
    journal = await _journal(session_factory)
    assert journal[0].posted_at is None


async def test_chat_language_is_used_in_reminders(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Тексты автосообщений берутся из языка интерфейса группы."""
    clock = FakeClock(datetime(2026, 10, 19, 20, 0, tzinfo=UTC))
    await _chat(session_factory, words=False)
    async with session_factory() as session:
        async with session.begin():
            await ChatRepo(session).update_settings(CHAT_ID, ui_language="ru")
    await _seed_streak(session_factory, days=2, last_active=date(2026, 10, 18))
    sender = RecordingSender()
    scheduler = _scheduler(session_factory, clock=clock, sender=sender)

    await scheduler.tick()

    assert "Огонёк группы под угрозой" in sender.texts[0]
