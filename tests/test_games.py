"""Тесты движка игр: квиз от старта до итогов, анти-обман, перезапуск.

Часы и отправщик подменяются: реального Telegram и реального времени
здесь нет. Ключевые правила проверяются на уровне БД — так же, как
это увидит продакшен: один ответ на раунд, дедлайн по `ends_at`,
бонус «первому» ровно одному, вторая игра в чате — отказ.
"""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import Chat, GameAnswer, GameSession, ScoreEvent
from bot.repositories import ChatRepo, ContentRepo, GameRepo, MemberRepo, ScoreRepo
from bot.services.ai_errors import AIError
from bot.services.content import ContentService
from bot.services.fake_ai_client import FakeAIClient
from bot.services.games.callbacks import build_answer_callback, parse_answer_callback
from bot.services.games.manager import GameManager
from bot.services.games.quiz import quiz_points
from bot.services.scoring import PERIOD_ALL, ScoringService
from bot.services.streak import StreakService

CHAT_ID = -100200


class FakeClock:
    """Управляемое «сейчас»: тест двигает время руками."""

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@dataclass
class SentMessage:
    chat_id: int
    text: str
    reply_markup: object
    thread_id: int | None


class RecordingSender:
    """Sender без Telegram: записывает отправки и правки."""

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.edited: list[tuple[int, int, str, object]] = []
        self._next_id = 100

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        reply_markup: object = None,
        reply_to_message_id: int | None = None,
    ) -> object:
        self._next_id += 1
        self.sent.append(SentMessage(chat_id, text, reply_markup, thread_id))
        return SimpleNamespace(message_id=self._next_id)

    async def reply(self, message: object, text: str, *, reply_markup: object = None) -> object:
        return await self.send(CHAT_ID, text, reply_markup=reply_markup)

    async def edit(
        self, chat_id: int, message_id: int, text: str, *, reply_markup: object = None
    ) -> None:
        self.edited.append((chat_id, message_id, text, reply_markup))

    async def answer_callback(self, callback: object, text: str | None = None) -> None:
        pass  # в тестах движка кнопок нет


def _settings(**overrides: object) -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None, **overrides)


def _manager(
    session_factory: async_sessionmaker[AsyncSession],
    sender: RecordingSender,
    settings: Settings,
    clock: FakeClock,
    ai: FakeAIClient | None = None,
) -> GameManager:
    content = ContentService(session_factory, ai or FakeAIClient(), settings)
    scoring = ScoringService(session_factory, settings, now=clock)
    streak = StreakService(session_factory, now=clock)
    return GameManager(
        session_factory=session_factory,
        settings=settings,
        sender=sender,
        content=content,
        scoring=scoring,
        streak=streak,
        now=clock,
    )


@contextlib.asynccontextmanager
async def _engine(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings | None = None,
    clock: FakeClock | None = None,
    ai: FakeAIClient | None = None,
):
    """Менеджер с записывающим отправщиком; таймеры гасятся на выходе."""
    sender = RecordingSender()
    clock = clock or FakeClock()
    games = _manager(session_factory, sender, settings or _settings(), clock, ai)
    try:
        yield sender, clock, games
    finally:
        await games.shutdown()


async def _chat(session_factory: async_sessionmaker[AsyncSession]) -> Chat:
    async with session_factory() as session:
        async with session.begin():
            chat = await ChatRepo(session).get_or_create(CHAT_ID, title="Games")
        return chat


async def _member(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    name: str,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await MemberRepo(session).get_or_create(CHAT_ID, user_id, display_name=name)


def _question(index: int) -> dict:
    return {
        "question": f"Question {index}: pick the right option.",
        "options": ["alpha", "beta", "gamma", "delta"],
        "correct_index": 1,  # всегда вариант B
        "explanation": f"Explanation {index}.",
    }


async def _seed_bank(
    session_factory: async_sessionmaker[AsyncSession], count: int
) -> None:
    """Вопросы в банк: движок берёт контент оттуда, без ИИ."""
    async with session_factory() as session:
        async with session.begin():
            repo = ContentRepo(session)
            for index in range(count):
                await repo.add_bank_item("quiz_batch", "B1", "en", _question(index))


def _session_id_of(message: SentMessage) -> uuid.UUID:
    """Сессия из callback_data первой кнопки сообщения раунда."""
    button = message.reply_markup.inline_keyboard[0][0]
    session_id, round_no, _choice = parse_answer_callback(button.callback_data)
    assert round_no == 1
    return session_id


async def _finish_round(
    games: GameManager, session_id: uuid.UUID, clock: FakeClock
) -> None:
    """Провести текущий раунд до конца: время вышло, пауза прошла."""
    clock.advance(30)  # раунд истёк (20 с) с запасом
    await games.advance(session_id)
    clock.advance(10)  # пауза (3 с) истекла
    await games.advance(session_id)


# --- 6.3–6.6: квиз от старта до таблицы результатов ---


async def test_quiz_runs_to_the_end_with_results(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _engine(session_factory) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=5)
        await _member(session_factory, 1, "Anna")
        await _member(session_factory, 2, "Bekzod")

        outcome = await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=3, thread_id=None
        )
        assert outcome.result.value == "started"
        assert len(sender.sent) == 1

        first = sender.sent[0]
        assert "Quiz — round 1/3" in first.text
        assert "Question 0" in first.text
        assert "A. alpha" in first.text
        buttons = [b for row in first.reply_markup.inline_keyboard for b in row]
        assert [b.text for b in buttons] == ["A", "B", "C", "D"]
        # Правильный ответ не попадает в callback_data.
        assert all(b.callback_data.startswith("g:") for b in buttons)
        session_id = _session_id_of(first)

        # Раунд 1: Анна отвечает верно, Бекзод — нет.
        clock.advance(2)
        good = await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=1, round_no=1, payload="1"
        )
        assert good.result.value == "correct"
        assert good.points == 10 + 4 + 2  # база + скорость + первый
        bad = await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=2, round_no=1, payload="2"
        )
        assert bad.result.value == "wrong"
        assert bad.points == 0

        # Раунд закрыт: кнопки убраны, ответ и пояснение показаны.
        await _finish_round(games, session_id, clock)
        assert len(sender.edited) == 1
        _chat_id, _msg, text, markup = sender.edited[0]
        assert markup is None
        assert "Correct: B. beta" in text
        assert "Explanation 0." in text
        assert "Anna" in text
        assert "Bekzod" not in text

        # Открылся второй раунд.
        assert len(sender.sent) == 2
        assert "Quiz — round 2/3" in sender.sent[1].text

        # Кнопки закрытого раунда больше не принимаются.
        stale = await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=1, round_no=1, payload="1"
        )
        assert stale.result.value == "round_over"

        # Оставшиеся раунды: Анна отвечает верно.
        for round_no in (2, 3):
            clock.advance(2)
            answer = await games.submit_answer(
                session_id, chat_id=CHAT_ID, user_id=1, round_no=round_no, payload="1"
            )
            assert answer.result.value == "correct"
            await _finish_round(games, session_id, clock)

        # Итоги: топ сессии и огонёк группы.
        results = sender.sent[-1]
        assert "Quiz finished!" in results.text
        assert "🥇 Anna — 48 pts" in results.text
        assert "Group flame: 1 🔥" in results.text

        # Сессия закрыта.
        async with session_factory() as session:
            row = await GameRepo(session).get(session_id)
        assert row is not None
        assert row.status == "finished"
        assert row.ends_at is None

    # Очки попали в рейтинг: 48 за ответы + 2 за участие; Бекзод — участие.
    scoring = ScoringService(session_factory, _settings())
    top = await scoring.top(await _chat(session_factory), PERIOD_ALL)
    assert {entry.user_id: entry.points for entry in top} == {1: 50, 2: 2}


async def test_participation_awarded_once_per_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Повторный finish не начисляет участие дважды (идемпотентность)."""
    async with _engine(session_factory) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await _member(session_factory, 1, "Anna")

        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])
        clock.advance(2)
        await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=1, round_no=1, payload="1"
        )
        await _finish_round(games, session_id, clock)

        # Досрочно дёргаем шаги закрытой сессии — ничего не должно начисляться.
        await games.advance(session_id)
        await games.advance(session_id)

    async with session_factory() as session:
        total = await ScoreRepo(session).total(CHAT_ID, 1)
    assert total == 16 + 2  # ответ + участие, без дублей


# --- 6.4, 6.9: один ответ на раунд, дедлайн, одновременные ответы ---


async def test_second_answer_in_round_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _engine(session_factory) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])

        clock.advance(2)
        first = await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=7, round_no=1, payload="1"
        )
        assert first.result.value == "correct"
        again = await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=7, round_no=1, payload="2"
        )
        assert again.result.value == "already"
        assert again.points == 0

    async with session_factory() as session:
        answers = list((await session.execute(select(GameAnswer))).scalars().all())
        events = list((await session.execute(select(ScoreEvent))).scalars().all())
    assert len(answers) == 1
    assert len(events) == 1  # очки не задвоились
    assert answers[0].points == events[0].points == 16


async def test_answer_after_deadline_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _engine(session_factory) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])

        # Время раунда истекло, но таймер его ещё не закрыл: ответ отклоняется.
        clock.advance(21)
        late = await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=7, round_no=1, payload="1"
        )
        assert late.result.value == "too_late"
        assert late.points == 0

    async with session_factory() as session:
        answers = list((await session.execute(select(GameAnswer))).scalars().all())
        events = list((await session.execute(select(ScoreEvent))).scalars().all())
    assert answers == []
    assert events == []


async def test_first_bonus_goes_to_exactly_one_player(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Одновременные верные ответы: +2 получает ровно один участник."""
    async with _engine(session_factory) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await _member(session_factory, 1, "Anna")
        await _member(session_factory, 2, "Bekzod")
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])

        clock.advance(2)
        outcomes = await asyncio.gather(
            games.submit_answer(
                session_id, chat_id=CHAT_ID, user_id=1, round_no=1, payload="1"
            ),
            games.submit_answer(
                session_id, chat_id=CHAT_ID, user_id=2, round_no=1, payload="1"
            ),
        )
        assert {o.result.value for o in outcomes} == {"correct"}
        assert sorted(o.points for o in outcomes) == [14, 16]

    async with session_factory() as session:
        scores = sorted(
            [
                await ScoreRepo(session).total(CHAT_ID, 1),
                await ScoreRepo(session).total(CHAT_ID, 2),
            ]
        )
    assert scores == [14, 16]


async def test_fast_answer_gets_no_speed_bonus(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Ответ быстрее 0,5 с после показа — без бонуса скорости."""
    async with _engine(session_factory) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])

        clock.advance(0.1)
        outcome = await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=7, round_no=1, payload="1"
        )
        assert outcome.result.value == "correct"
        assert outcome.points == 10 + 2  # база + первый, скорость отнята


# --- 6.2, 6.9: перезапуск бота посреди игры ---


async def test_recovery_closes_expired_round_and_continues(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Пока бот «лежал», раунд истёк: recover закрывает его и продолжает."""
    clock = FakeClock()
    async with _engine(session_factory, clock=clock) as (sender, _c, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=5)
        await _member(session_factory, 1, "Anna")
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=2, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])
        clock.advance(2)
        await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=1, round_no=1, payload="1"
        )

    # «Перезапуск»: новый менеджер, те же часы и база.
    async with _engine(session_factory, clock=clock) as (sender2, _c, games2):
        clock.advance(40)  # и раунд, и пауза давно истекли
        await games2.recover()

        # Раунд 1 закрыт: сообщение перерисовано, очки на месте.
        assert len(sender2.edited) == 1
        assert "Correct: B. beta" in sender2.edited[0][2]
        async with session_factory() as session:
            row = await GameRepo(session).get(session_id)
        assert row is not None and row.status == "running"
        assert row.state["phase"] == "break"

        # Игра продолжается: открывается раунд 2.
        clock.advance(5)
        await games2.advance(session_id)
        assert len(sender2.sent) == 1
        assert "Quiz — round 2/2" in sender2.sent[0].text

    async with session_factory() as session:
        assert await ScoreRepo(session).total(CHAT_ID, 1) == 16


async def test_recovery_rearms_live_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Живая сессия возобновляется: раунд не закрывается раньше времени."""
    clock = FakeClock()
    async with _engine(session_factory, clock=clock) as (sender, _c, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])

    async with _engine(session_factory, clock=clock) as (sender2, clock2, games2):
        await games2.recover()
        # Срок раунда не истёк — ничего не закрыто и не отправлено.
        assert sender2.edited == []
        assert sender2.sent == []

        clock2.advance(30)  # теперь раунд просрочен
        await games2.advance(session_id)
        assert len(sender2.edited) == 1

        clock2.advance(10)  # пауза истекла — единственный раунд, значит итоги
        await games2.advance(session_id)
        assert "Quiz finished!" in sender2.sent[-1].text

    async with session_factory() as session:
        row = await GameRepo(session).get(session_id)
    assert row is not None and row.status == "finished"


async def test_recovery_closes_game_in_inactive_chat(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Бота удалили из группы: сессия закрывается молча, очки остаются."""
    clock = FakeClock()
    async with _engine(session_factory, clock=clock) as (sender, _c, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])

    async with session_factory() as session:
        async with session.begin():
            await ChatRepo(session).set_active(CHAT_ID, False)

    async with _engine(session_factory, clock=clock) as (sender2, _c, games2):
        await games2.recover()
        assert sender2.edited == []  # в недоступный чат ничего не шлём
        assert sender2.sent == []

    async with session_factory() as session:
        row = await GameRepo(session).get(session_id)
    assert row is not None and row.status == "stopped"


# --- 6.1, 6.7: блокировка чата, кулдаун, дневной лимит ---


async def test_second_game_in_chat_refused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _engine(session_factory) as (sender, _clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=5)
        first = await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        assert first.result.value == "started"

        second = await games.start_quiz(
            await _chat(session_factory), started_by=2, rounds=1, thread_id=None
        )
        assert second.result.value == "already_running"

    async with session_factory() as session:
        rows = list((await session.execute(select(GameSession))).scalars().all())
    assert len(rows) == 1  # вторая сессия не создалась


async def test_cooldown_between_games(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings(limit_game_cooldown_seconds=60, limit_games_per_day=10)
    async with _engine(session_factory, settings=settings) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])
        await _finish_round(games, session_id, clock)

        again = await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        assert again.result.value == "cooldown"


async def test_daily_game_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings(limit_game_cooldown_seconds=0, limit_games_per_day=1)
    async with _engine(session_factory, settings=settings) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=1)
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])
        await _finish_round(games, session_id, clock)

        next_game = await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=1, thread_id=None
        )
        assert next_game.result.value == "daily_limit"


async def test_no_content_refusal_with_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Банк пуст и ИИ недоступен — запуск честно отказывает с причиной."""
    ai = FakeAIClient(error=AIError("server down"))
    async with _engine(session_factory, ai=ai) as (sender, _clock, games):
        await _chat(session_factory)
        outcome = await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=5, thread_id=None
        )
        assert outcome.result.value == "no_content"
        assert outcome.reason == "ai_unavailable"
        assert sender.sent == []


# --- Остановка игры ---


async def test_stop_mid_game_keeps_points(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _engine(session_factory) as (sender, clock, games):
        await _chat(session_factory)
        await _seed_bank(session_factory, count=5)
        await _member(session_factory, 1, "Anna")
        await games.start_quiz(
            await _chat(session_factory), started_by=1, rounds=2, thread_id=None
        )
        session_id = _session_id_of(sender.sent[0])

        clock.advance(2)
        await games.submit_answer(
            session_id, chat_id=CHAT_ID, user_id=1, round_no=1, payload="1"
        )
        clock.advance(30)
        await games.advance(session_id)  # раунд 1 закрыт, идёт пауза

        assert await games.stop(CHAT_ID) is True
        assert await games.stop(CHAT_ID) is False  # второго раза нечего останавливать

        # Сообщение раунда перерисовано без кнопок и помечено остановкой.
        assert len(sender.edited) == 2
        stopped_text = sender.edited[-1][2]
        assert "Game stopped" in stopped_text
        assert sender.edited[-1][3] is None
        # Второй раунд так и не открылся.
        assert len(sender.sent) == 1

    async with session_factory() as session:
        row = await GameRepo(session).get(session_id)
        total = await ScoreRepo(session).total(CHAT_ID, 1)
    assert row is not None and row.status == "stopped"
    assert total == 16  # очки закрытого раунда сохранены


# --- Чистые функции: очки, callback_data ---


def test_quiz_points_speed_and_first_bonuses() -> None:
    settings = _settings()

    # Мгновенный ответ подозрителен: скорость не поощряем.
    assert (
        quiz_points(
            settings,
            answer_time_ms=100,
            duration_seconds=20,
            is_correct=True,
            is_first=False,
        )
        == 10
    )
    # Через 2 секунды из 20: линейный бонус int(5 * 18/20) = 4.
    assert (
        quiz_points(
            settings,
            answer_time_ms=2000,
            duration_seconds=20,
            is_correct=True,
            is_first=True,
        )
        == 10 + 4 + 2
    )
    # В последнюю секунду бонуса уже нет.
    assert (
        quiz_points(
            settings,
            answer_time_ms=20000,
            duration_seconds=20,
            is_correct=True,
            is_first=False,
        )
        == 10
    )
    # Неверный ответ — ноль.
    assert (
        quiz_points(
            settings,
            answer_time_ms=2000,
            duration_seconds=20,
            is_correct=False,
            is_first=False,
        )
        == 0
    )


def test_answer_callback_roundtrip() -> None:
    session_id = uuid.uuid4()

    data = build_answer_callback(session_id, 3, 2)
    assert parse_answer_callback(data) == (session_id, 3, 2)

    # Чужие и повреждённые данные молча игнорируются.
    assert parse_answer_callback("set:level:B1") is None
    assert parse_answer_callback("g:not-a-uuid:1:1") is None
    assert parse_answer_callback("g:abc:1") is None
    assert parse_answer_callback(None) is None
