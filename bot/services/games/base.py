"""Базовый класс игр: общая механика сессий и раундов.

Оркестрацию (блокировка чата, таймеры, отправка сообщений, восстановление
после перезапуска) ведёт `GameManager`; здесь — работа с состояниями
сессии, приём ответов и начисление очков. Aiogram-объекты не нужны:
всё это тестируется без Telegram.

Состояние сессии лежит в `game_sessions.state` (JSON), общие поля:

- `phase`: `"question"` (идёт раунд) или `"break"` (пауза между раундами);
- `current`: номер текущего раунда, начиная с 1;
- `total`: сколько раундов всего;
- `items`: контент раундов, `items[n-1]` — раунд `n`;
- `messages`: `{"<round>": message_id}` — сообщения раундов;
- `round_started`: ISO-время старта текущего раунда (бонус за скорость);
- `duration` и `break`: секунды на раунд и на паузу;
- `lang` и `thread_id`: язык интерфейса и тема форума на момент старта.

Источник правды по таймерам — `game_sessions.ends_at`: раунд закрывается,
когда это время проходит по серверным часам, даже если бот перезапускался.
"""

import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import ClassVar

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import Chat, utcnow
from bot.repositories import ChatRepo, GameRepo, RoundAnswer, SessionTotal
from bot.services.scoring import ScoringService

logger = logging.getLogger(__name__)

# Фазы сессии.
PHASE_QUESTION = "question"
PHASE_BREAK = "break"


class StartResult(Enum):
    """Итог попытки запустить игру."""

    STARTED = "started"
    ALREADY_RUNNING = "already_running"
    COOLDOWN = "cooldown"
    DAILY_LIMIT = "daily_limit"
    NO_CONTENT = "no_content"


@dataclass(frozen=True)
class StartOutcome:
    """Результат запуска игры и причина, если начаться не получилось."""

    result: StartResult
    # Почему нет контента: quota, ai_unavailable, empty, send_failed.
    reason: str | None = None


def refusal_key(outcome: StartOutcome) -> str:
    """Ключ локализации для причины, по которой игра не началась."""
    if outcome.result is StartResult.ALREADY_RUNNING:
        return "game.already_running"
    if outcome.result is StartResult.COOLDOWN:
        return "game.cooldown"
    if outcome.result is StartResult.DAILY_LIMIT:
        return "game.daily_limit"
    if outcome.reason == "quota":
        return "game.no_content_quota"
    if outcome.reason == "send_failed":
        return "common.error"
    return "game.no_content"


class AnswerResult(Enum):
    """Итог нажатия кнопки ответа."""

    NO_GAME = "no_game"  # сессии нет или уже закрыта
    ROUND_OVER = "round_over"  # раунд не текущий или не в фазе вопроса
    TOO_LATE = "too_late"  # время раунда истекло
    ALREADY = "already"  # участник уже отвечал в этом раунде
    CORRECT = "correct"
    WRONG = "wrong"


def aware(moment: datetime) -> datetime:
    """SQLite отдаёт время без зоны: такое время считаем UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def round_started_at(state: dict) -> datetime | None:
    """Время старта текущего раунда из состояния сессии."""
    raw = state.get("round_started")
    if not raw:
        return None
    try:
        return aware(datetime.fromisoformat(raw))
    except (TypeError, ValueError):
        return None


class Game(ABC):
    """Механика раундов, общая для всех игр; специфику дают подклассы."""

    kind: ClassVar[str] = ""
    # Имя настройки с длительностью раунда: каждая игра задаёт своё время.
    duration_setting: ClassVar[str] = "quiz_round_seconds"
    # Scramble закрывает раунд сразу после первого верного ответа.
    close_on_correct: ClassVar[bool] = False
    # Scramble принимает текстовые ответы reply на сообщение раунда.
    accepts_text: ClassVar[bool] = False

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        scoring: ScoringService,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._scoring = scoring
        self._now = now

    # --- Специфика игры ---

    @abstractmethod
    async def prepare(self, chat: Chat, raw_items: list[dict]) -> list[dict]:
        """Проверяет контент и возвращает элементы раундов.

        Может вернуть меньше, чем пришло: невалидное отбрасывается
        (схемы и банк вопросов — первая линия проверки, здесь последняя).
        """

    @abstractmethod
    def render_round(
        self, state: dict, round_no: int, lang: str, session_id: uuid.UUID
    ) -> "RoundView":
        """Текст и кнопки раунда."""

    @abstractmethod
    def check_answer(self, item: dict, payload: str) -> bool:
        """Верен ли ответ; `payload` — индекс варианта строкой."""

    @abstractmethod
    def answer_label(self, item: dict, payload: str) -> str:
        """Что записать в `game_answers.answer` (например, буква)."""

    @abstractmethod
    def points_for(
        self,
        item: dict,
        *,
        answer_time_ms: int,
        duration_seconds: int,
        is_correct: bool,
        is_first: bool,
    ) -> int:
        """Очки за ответ: база, бонус скорости, бонус первому."""

    @abstractmethod
    def render_round_result(
        self, state: dict, round_no: int, answers: list[RoundAnswer], lang: str
    ) -> str:
        """Сообщение после раунда: верный ответ, пояснение, кто ответил верно."""

    @abstractmethod
    def render_results(
        self, totals: list[SessionTotal], lang: str, flame_days: int | None
    ) -> str:
        """Итоговое сообщение сессии: топ и огонёк группы."""

    # --- Общее: ответы и переходы раундов ---

    async def handle_answer(
        self,
        session_id: uuid.UUID,
        *,
        chat_id: int,
        user_id: int,
        round_no: int,
        payload: str,
    ) -> "AnswerOutcome":
        """Принимает ответ участника: одна короткая транзакция на нажатие.

        Проверяет сессию, раунд и время по серверным часам, считает очки
        (бонус «первому» — под блокировкой строки сессии) и пишет ответ
        вместе с начислением в журнал.
        """
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    return await self._answer_tx(
                        session,
                        session_id,
                        chat_id=chat_id,
                        user_id=user_id,
                        round_no=round_no,
                        payload=payload,
                    )
        except IntegrityError:
            # Уникальный индекс (сессия, раунд, участник): повторное или
            # параллельное нажатие. Транзакция откатилась целиком —
            # очки не задвоились.
            logger.info("Повторный ответ в раунде %s (сессия %s)", round_no, session_id)
            return AnswerOutcome(AnswerResult.ALREADY)

    async def _answer_tx(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
        *,
        chat_id: int,
        user_id: int,
        round_no: int,
        payload: str,
    ) -> "AnswerOutcome":
        repo = GameRepo(session)
        game = await repo.get(session_id, for_update=True)
        if game is None or game.chat_id != chat_id or game.status != "running":
            return AnswerOutcome(AnswerResult.NO_GAME)
        state = game.state
        if state.get("phase") != PHASE_QUESTION or state.get("current") != round_no:
            return AnswerOutcome(AnswerResult.ROUND_OVER)

        # Источник правды по времени — сервер и `ends_at` в БД.
        now = self._now()
        if game.ends_at is None or now > aware(game.ends_at):
            return AnswerOutcome(AnswerResult.TOO_LATE)
        if await repo.get_answer(session_id, round_no, user_id) is not None:
            return AnswerOutcome(AnswerResult.ALREADY)

        items = state.get("items") or []
        if not 1 <= round_no <= len(items):
            return AnswerOutcome(AnswerResult.ROUND_OVER)
        item = items[round_no - 1]

        is_correct = self.check_answer(item, payload)
        # Бонус «первому» получает ровно один: верные ответы считаются
        # под блокировкой строки сессии.
        is_first = is_correct and (await repo.count_correct_answers(session_id, round_no)) == 0

        started = round_started_at(state)
        if started is not None:
            elapsed_ms = max(int((now - started).total_seconds() * 1000), 0)
        else:  # время старта потерялось — бонуса скорости не будет
            elapsed_ms = int(state.get("duration", 0)) * 1000
        duration_seconds = int(state.get("duration", 0))
        points = self.points_for(
            item,
            answer_time_ms=elapsed_ms,
            duration_seconds=duration_seconds,
            is_correct=is_correct,
            is_first=is_first,
        )
        answer = await repo.add_answer(
            session_id,
            round_no,
            user_id=user_id,
            answer=self.answer_label(item, payload),
            is_correct=is_correct,
            answer_time_ms=elapsed_ms,
            points=points,
        )
        chat = await ChatRepo(session).get(game.chat_id)
        timezone_name = chat.timezone if chat is not None else "UTC"
        awarded = await self._scoring.award_locked(
            session,
            timezone_name,
            game.chat_id,
            user_id,
            points,
            self.kind,
            session_id,
        )
        answer.points = awarded
        return AnswerOutcome(
            AnswerResult.CORRECT if is_correct else AnswerResult.WRONG, points=awarded
        )

    async def close_round(self, session_id: uuid.UUID, round_no: int) -> "RoundClosed | None":
        """Закрывает раунд: фаза → пауза, новое `ends_at`.

        Идемпотентно: если раунд уже закрыт или сессия не идёт — None.
        """
        async with self._session_factory() as session:
            async with session.begin():
                repo = GameRepo(session)
                game = await repo.get(session_id, for_update=True)
                if game is None or game.status != "running":
                    return None
                state = dict(game.state)
                if state.get("phase") != PHASE_QUESTION or state.get("current") != round_no:
                    return None
                answers = await repo.round_answers(session_id, round_no)
                state["phase"] = PHASE_BREAK
                state["round_started"] = None
                ends_at = self._now() + timedelta(seconds=int(state.get("break", 3)))
                game.ends_at = ends_at
                await repo.update_state(session_id, state)
                return RoundClosed(
                    state=state,
                    answers=answers,
                    chat_id=game.chat_id,
                    message_id=(state.get("messages") or {}).get(str(round_no)),
                    ends_at=ends_at,
                )

    async def open_round(self, session_id: uuid.UUID, round_no: int) -> "RoundOpened | None":
        """Открывает следующий раунд: фаза → вопрос, новое `ends_at`.

        Идемпотентно: открывается только `current + 1` из фазы паузы.
        """
        async with self._session_factory() as session:
            async with session.begin():
                repo = GameRepo(session)
                game = await repo.get(session_id, for_update=True)
                if game is None or game.status != "running":
                    return None
                state = dict(game.state)
                if state.get("phase") != PHASE_BREAK or state.get("current", 0) + 1 != round_no:
                    return None
                if not 1 <= round_no <= int(state.get("total", 0)):
                    return None
                started = self._now()
                state["phase"] = PHASE_QUESTION
                state["current"] = round_no
                state["round_started"] = started.isoformat()
                ends_at = started + timedelta(seconds=int(state.get("duration", 20)))
                game.ends_at = ends_at
                await repo.update_state(session_id, state)
                return RoundOpened(state=state, ends_at=ends_at)

    async def save_round_message(
        self, session_id: uuid.UUID, round_no: int, message_id: int
    ) -> None:
        """Запоминает сообщение раунда: по нему бот потом уберёт кнопки."""
        async with self._session_factory() as session:
            async with session.begin():
                repo = GameRepo(session)
                game = await repo.get(session_id, for_update=True)
                if game is None or game.status != "running":
                    return
                state = dict(game.state)
                messages = dict(state.get("messages") or {})
                messages[str(round_no)] = message_id
                state["messages"] = messages
                await repo.update_state(session_id, state)

    async def finish(self, session_id: uuid.UUID) -> "Finished | None":
        """Завершает сессию и начисляет очки за участие.

        Участие получает каждый, кто ответил хотя бы раз. Повторный вызов
        ничего не начисляет: закрытая сессия не проходит проверку статуса.
        """
        async with self._session_factory() as session:
            async with session.begin():
                repo = GameRepo(session)
                game = await repo.get(session_id, for_update=True)
                if game is None or game.status != "running":
                    return None
                state = game.state
                if state.get("phase") != PHASE_BREAK or state.get("current") != state.get("total"):
                    return None
                chat = await ChatRepo(session).get(game.chat_id)
                timezone_name = chat.timezone if chat is not None else "UTC"
                totals = await repo.session_totals(session_id)
                game.status = "finished"
                game.ends_at = None
                game.finished_at = self._now()
                participation = self._settings.score_participation
                for total in totals:
                    await self._scoring.award_locked(
                        session,
                        timezone_name,
                        game.chat_id,
                        total.user_id,
                        participation,
                        self.kind,
                        session_id,
                    )
                return Finished(
                    totals=totals,
                    chat_id=game.chat_id,
                    lang=str(state.get("lang", "en")),
                    thread_id=state.get("thread_id"),
                    timezone=timezone_name,
                )


# --- Структуры, которыми игра и менеджер обмениваются результатами шагов ---


@dataclass(frozen=True)
class AnswerOutcome:
    """Итог ответа: результат и сколько очков реально начислено."""

    result: AnswerResult
    points: int = 0


@dataclass(frozen=True)
class RoundView:
    """Сообщение раунда: текст и кнопки (у scramble кнопок нет)."""

    text: str
    keyboard: InlineKeyboardMarkup | None


@dataclass(frozen=True)
class RoundClosed:
    """Данные закрытого раунда для правки сообщения."""

    state: dict
    answers: list[RoundAnswer]
    chat_id: int
    message_id: int | None
    ends_at: datetime


@dataclass(frozen=True)
class RoundOpened:
    """Данные открытого раунда: состояние и дедлайн."""

    state: dict
    ends_at: datetime


@dataclass(frozen=True)
class Finished:
    """Данные завершённой сессии для сообщения с итогами."""

    totals: list[SessionTotal]
    chat_id: int
    lang: str
    thread_id: int | None
    timezone: str
