"""Менеджер игр: блокировка чата, таймеры, восстановление, отправка.

Правила, которые здесь соблюдаются:

1. Одна активная игра на чат. Гарантия — частичный уникальный индекс
   в БД; менеджер добавляет кулдаун между играми и дневной лимит.
2. Источник правды для таймеров — `ends_at` в БД. `asyncio`-задачи
   только ждут и зовут `advance()`: даже после перезапуска бота раунды
   закрываются вовремя, а не «по памяти процесса».
3. `advance()` идемпотентен: шаг выполняется только когда срок текущей
   фазы истёк. `recover()` при старте закрывает просроченное и
   возобновляет живые сессии.
4. Все транзакции движка идут под внутренним `gate` — на SQLite в
   разработке все сессии делят одно соединение, а в PostgreSQL блокирует
   сама БД (`FOR UPDATE` и уникальные индексы).
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.locales import t
from bot.models import Chat, utcnow
from bot.repositories import ChatRepo, GameRepo
from bot.sender import ChatUnavailable, Sender
from bot.services.content import ContentResult, ContentService
from bot.services.games.base import (
    PHASE_BREAK,
    PHASE_QUESTION,
    AnswerOutcome,
    AnswerResult,
    Finished,
    Game,
    RoundClosed,
    RoundOpened,
    RoundView,
    StartOutcome,
    StartResult,
    aware,
)
from bot.services.games.gap import GapGame
from bot.services.games.quiz import QuizGame
from bot.services.games.race import RaceGame
from bot.services.games.scramble import ScrambleGame
from bot.services.games.truefalse import TrueFalseGame
from bot.services.scoring import ScoringService, day_bounds
from bot.services.streak import StreakService

logger = logging.getLogger(__name__)

# Мини-игры, доступные через `/games` и отдельные команды.
MINI_GAME_KINDS: tuple[str, ...] = ("scramble", "gap", "race", "truefalse")


class GameManager:
    """Жизненный цикл игр: запуск, раунды, таймеры, итоги, остановка."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        sender: Sender,
        content: ContentService,
        scoring: ScoringService,
        streak: StreakService,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._sender = sender
        self._content = content
        self._scoring = scoring
        self._streak = streak
        self._now = now
        # Одна транзакция движка за раз: на общем SQLite-соединении
        # транзакции не должны переплетаться.
        self._gate = asyncio.Lock()
        self._timers: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._games: dict[str, Game] = {}
        for game_class in (QuizGame, ScrambleGame, GapGame, RaceGame, TrueFalseGame):
            game = game_class(
                session_factory=session_factory,
                settings=settings,
                scoring=scoring,
                now=now,
            )
            self._games[game.kind] = game

    # --- Запуск игр ---

    async def start_quiz(
        self, chat: Chat, *, started_by: int, rounds: int, thread_id: int | None
    ) -> StartOutcome:
        """Запускает квиз: проверки, контент, сессия в БД, первый раунд."""
        return await self._start_game(
            chat,
            "quiz",
            started_by=started_by,
            thread_id=thread_id,
            rounds=rounds,
        )

    async def start_mini_game(
        self, chat: Chat, kind: str, *, started_by: int, thread_id: int | None
    ) -> StartOutcome:
        """Запускает мини-игру из /games или отдельной команды."""
        if kind not in MINI_GAME_KINDS:
            return StartOutcome(StartResult.NO_CONTENT, "unknown")
        return await self._start_game(chat, kind, started_by=started_by, thread_id=thread_id)

    async def _start_game(
        self,
        chat: Chat,
        kind: str,
        *,
        started_by: int,
        thread_id: int | None,
        rounds: int | None = None,
    ) -> StartOutcome:
        """Общий старт: проверки, контент, сессия в БД, первый раунд."""
        game = self._games.get(kind)
        if game is None:
            logger.warning("Неизвестная игра %r", kind)
            return StartOutcome(StartResult.NO_CONTENT, "unknown")

        refusal = await self._start_refusal(chat)
        if refusal is not None:
            return refusal

        # Контент готовится вне блокировок: ИИ может думать секунды.
        content = await self._fetch_content(chat, kind, rounds)
        if not content.items:
            return StartOutcome(StartResult.NO_CONTENT, content.reason)
        items = await game.prepare(chat, content.items)
        if not items:
            return StartOutcome(StartResult.NO_CONTENT, "empty")

        duration = int(getattr(self._settings, game.duration_setting))
        now = self._now()
        state = {
            "phase": PHASE_QUESTION,
            "current": 1,
            "total": len(items),
            "items": items,
            "messages": {},
            "round_started": now.isoformat(),
            "duration": duration,
            "break": self._settings.quiz_break_seconds,
            "lang": chat.ui_language,
            "thread_id": thread_id,
        }
        ends_at = now + timedelta(seconds=duration)
        try:
            async with self._gate:
                async with self._session_factory() as session:
                    async with session.begin():
                        created = await GameRepo(session).create_session(
                            chat.id,
                            kind=kind,
                            started_by=started_by,
                            state=state,
                            ends_at=ends_at,
                        )
                        session_id = created.id
        except IntegrityError:
            # Гонка запуска: кто-то уже начал игру в этом чате.
            return StartOutcome(StartResult.ALREADY_RUNNING)

        if not await self._launch_round(game, session_id, chat.id, state, ends_at):
            return StartOutcome(StartResult.NO_CONTENT, "send_failed")
        return StartOutcome(StartResult.STARTED)

    async def _fetch_content(self, chat: Chat, kind: str, rounds: int | None) -> ContentResult:
        """Контент конкретной игры: квиз берёт нужное число раундов."""
        if kind == "quiz":
            return await self._content.get_quiz(chat, rounds or 1)
        getter = getattr(self._content, f"get_{kind}")
        return await getter(chat)

    async def _start_refusal(self, chat: Chat) -> StartOutcome | None:
        """Проверки до запуска: активная игра, кулдаун, дневной лимит."""
        now = self._now()
        async with self._gate:
            async with self._session_factory() as session:
                repo = GameRepo(session)
                if await repo.get_active(chat.id) is not None:
                    return StartOutcome(StartResult.ALREADY_RUNNING)
                last_end = await repo.latest_finished_at(chat.id)
                if last_end is not None:
                    since_end = (now - aware(last_end)).total_seconds()
                    if since_end < self._settings.limit_game_cooldown_seconds:
                        return StartOutcome(StartResult.COOLDOWN)
                day_start, _ = day_bounds(chat.timezone, now)
                started_today = await repo.count_started_since(chat.id, day_start)
        limit = self._settings.limit_games_per_day
        if limit > 0 and started_today >= limit:
            return StartOutcome(StartResult.DAILY_LIMIT)
        return None

    async def _launch_round(
        self,
        game: Game,
        session_id: uuid.UUID,
        chat_id: int,
        state: dict,
        ends_at: datetime,
    ) -> bool:
        """Отправляет сообщение текущего раунда и взводит таймер."""
        round_no = int(state["current"])
        lang = str(state.get("lang", "en"))
        view: RoundView = game.render_round(state, round_no, lang, session_id)
        try:
            message = await self._sender.send(
                chat_id,
                view.text,
                thread_id=state.get("thread_id"),
                reply_markup=view.keyboard,
            )
        except ChatUnavailable as exc:
            await self._abandon(session_id, chat_id, exc)
            return False
        except TelegramBadRequest as exc:
            logger.warning("Раунд не отправлен, сессия закрывается: %s", exc)
            await self._close_quietly(session_id)
            return False
        if message is not None:
            async with self._gate:
                await game.save_round_message(session_id, round_no, message.message_id)
        self._arm(session_id, ends_at)
        return True

    # --- Ответы ---

    async def submit_answer(
        self,
        session_id: uuid.UUID,
        *,
        chat_id: int,
        user_id: int,
        round_no: int,
        payload: str,
    ) -> AnswerOutcome:
        """Ответ участника: проверки и очки — одной транзакцией в БД."""
        async with self._gate:
            game = await self._game_of_session(session_id)
            if game is None:
                return AnswerOutcome(AnswerResult.NO_GAME)
            outcome = await game.handle_answer(
                session_id,
                chat_id=chat_id,
                user_id=user_id,
                round_no=round_no,
                payload=payload,
            )
        if outcome.result is AnswerResult.CORRECT and game.close_on_correct:
            await self._close_round(game, session_id, round_no)
        return outcome

    async def submit_text_answer(
        self,
        *,
        chat_id: int,
        user_id: int,
        reply_to_message_id: int,
        text: str,
    ) -> AnswerOutcome | None:
        """Текстовый ответ на сообщение игры; None — это не ответ игре.

        Privacy mode: бот видит только reply на свои сообщения. Совпадение
        с сообщением текущего раунда проверяется по состоянию сессии.
        """
        async with self._gate:
            async with self._session_factory() as session:
                row = await GameRepo(session).get_active(chat_id)
            if row is None:
                return None
            game = self._games.get(row.kind)
            if game is None or not game.accepts_text:
                return None
            state = row.state or {}
            if state.get("phase") != PHASE_QUESTION:
                return None
            round_no = int(state.get("current", 0))
            message_id = (state.get("messages") or {}).get(str(round_no))
            if message_id is None or int(message_id) != reply_to_message_id:
                return None
            outcome = await game.handle_answer(
                row.id,
                chat_id=chat_id,
                user_id=user_id,
                round_no=round_no,
                payload=text,
            )
        if outcome.result is AnswerResult.CORRECT and game.close_on_correct:
            await self._close_round(game, row.id, round_no)
        return outcome

    # --- Шаги сессии: advance ---

    async def advance(self, session_id: uuid.UUID) -> None:
        """Один шаг сессии; вызывается таймером и `recover()`.

        Идемпотентно: действие выполняется только если срок текущей
        фазы (`ends_at`) уже истёк, иначе таймер просто перевзводится.
        """
        async with self._lock_for(session_id):
            await self._step(session_id)

    async def _step(self, session_id: uuid.UUID) -> None:
        async with self._gate:
            async with self._session_factory() as session:
                row = await GameRepo(session).get(session_id)
        if row is None or row.status != "running":
            self._forget(session_id)
            return

        ends_at = aware(row.ends_at) if row.ends_at is not None else None
        state = row.state
        if ends_at is not None and ends_at > self._now():
            self._arm(session_id, ends_at)
            return

        game = self._games.get(row.kind)
        if game is None:
            logger.warning("Нет реализации игры %r, сессия %s закрывается", row.kind, session_id)
            await self._close_quietly(session_id)
            return

        phase = state.get("phase")
        current = int(state.get("current", 1))
        if phase == PHASE_QUESTION:
            await self._close_round(game, session_id, current)
        elif phase == PHASE_BREAK:
            if current >= int(state.get("total", 1)):
                await self._finish_session(game, session_id)
            else:
                await self._open_round(game, session_id, current + 1, row.chat_id)
        else:
            logger.warning("Неизвестная фаза %r в сессии %s", phase, session_id)
            await self._close_quietly(session_id)

    async def _close_round(self, game: Game, session_id: uuid.UUID, round_no: int) -> None:
        """Закрывает раунд: правит сообщение, взводит таймер паузы."""
        async with self._gate:
            closed: RoundClosed | None = await game.close_round(session_id, round_no)
        if closed is None:
            # Раунд закрыли параллельно или сессия уже не идёт.
            await self._rearm(session_id)
            return
        if closed.message_id is not None:
            lang = str(closed.state.get("lang", "en"))
            text = game.render_round_result(closed.state, round_no, closed.answers, lang)
            try:
                await self._sender.edit(closed.chat_id, closed.message_id, text)
            except ChatUnavailable as exc:
                await self._abandon(session_id, closed.chat_id, exc)
                return
            except TelegramBadRequest as exc:
                # Сообщение раунда удалили — игра продолжается без правки.
                logger.warning("Не удалось править сообщение раунда: %s", exc)
        self._arm(session_id, closed.ends_at)

    async def _open_round(
        self, game: Game, session_id: uuid.UUID, round_no: int, chat_id: int
    ) -> None:
        """Открывает следующий раунд и отправляет его сообщение."""
        async with self._gate:
            opened: RoundOpened | None = await game.open_round(session_id, round_no)
        if opened is None:
            await self._rearm(session_id)
            return
        await self._launch_round(game, session_id, chat_id, opened.state, opened.ends_at)

    async def _finish_session(self, game: Game, session_id: uuid.UUID) -> None:
        """Завершает сессию: очки за участие, огонёк, итоги."""
        async with self._gate:
            finished: Finished | None = await game.finish(session_id)
        if finished is None:
            await self._rearm(session_id)
            return
        self._cancel_timer(session_id)

        flame: int | None = None
        try:
            async with self._gate:
                async with self._session_factory() as session:
                    async with session.begin():
                        flame = await self._streak.register_activity_locked(
                            session, finished.chat_id, finished.timezone
                        )
        except Exception:
            logger.exception("Не удалось обновить огонёк чата %s", finished.chat_id)

        text = game.render_results(finished.totals, finished.lang, flame)
        try:
            await self._sender.send(finished.chat_id, text, thread_id=finished.thread_id)
        except ChatUnavailable as exc:
            await self._deactivate(finished.chat_id)
            logger.warning("Итоги не отправлены, чат недоступен: %s", exc)
        except TelegramBadRequest as exc:
            logger.warning("Итоги не отправлены: %s", exc)
        self._forget(session_id)

    # --- Остановка и восстановление ---

    async def stop(self, chat_id: int) -> bool:
        """Останавливает активную игру чата: очки закрытых раундов остаются.

        Сообщение текущего раунда перерисовывается без кнопок, чтобы
        участники не продолжали отвечать в закрытую игру.
        """
        async with self._gate:
            async with self._session_factory() as session:
                async with session.begin():
                    repo = GameRepo(session)
                    row = await repo.get_active(chat_id)
                    if row is None:
                        return False
                    session_id = row.id
                    kind = row.kind
                    state = dict(row.state)
                    await repo.close(session_id, "stopped")
        self._cancel_timer(session_id)
        game = self._games.get(kind)
        current = int(state.get("current", 1))
        message_id = (state.get("messages") or {}).get(str(current))
        if game is not None and message_id is not None:
            lang = str(state.get("lang", "en"))
            view = game.render_round(state, current, lang, session_id)
            text = f"{view.text}\n\n{t('game.stopped', lang)}"
            try:
                await self._sender.edit(chat_id, message_id, text)
            except (ChatUnavailable, TelegramBadRequest) as exc:
                logger.warning("Не получилось пометить игру остановленной: %s", exc)
        self._forget(session_id)
        return True

    async def recover(self) -> None:
        """При запуске бота: закрыть просроченное, возобновить живое.

        Чаты, из которых бота удалили (строка неактивна), закрываются
        молча: сессия получает статус `stopped`, очки сохраняются.
        """
        async with self._gate:
            async with self._session_factory() as session:
                running = await GameRepo(session).list_running()
        logger.info("Восстановление игр: активных сессий %d", len(running))
        for row in running:
            if not await self._chat_is_active(row.chat_id):
                logger.info("Чат %s неактивен, сессия %s закрывается", row.chat_id, row.id)
                await self._close_quietly(row.id)
                continue
            await self.advance(row.id)

    async def shutdown(self) -> None:
        """Остановка бота: таймеры снимаются, сессии остаются в БД."""
        tasks = list(self._timers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._timers.clear()

    # --- Внутренняя механика ---

    async def _game_of_session(self, session_id: uuid.UUID) -> Game | None:
        """Игра по типу сессии (вызывается под gate)."""
        async with self._session_factory() as session:
            row = await GameRepo(session).get(session_id)
        if row is None:
            return None
        game = self._games.get(row.kind)
        if game is None:
            logger.warning("Нет реализации игры %r для сессии %s", row.kind, session_id)
        return game

    async def _chat_is_active(self, chat_id: int) -> bool:
        async with self._gate:
            async with self._session_factory() as session:
                chat = await ChatRepo(session).get(chat_id)
        return chat is not None and chat.active

    async def _rearm(self, session_id: uuid.UUID) -> None:
        """Перечитывает сессию и взводит таймер по текущему `ends_at`."""
        async with self._gate:
            async with self._session_factory() as session:
                row = await GameRepo(session).get(session_id)
        if row is None or row.status != "running":
            self._forget(session_id)
            return
        if row.ends_at is None:
            logger.warning("Сессия %s без ends_at, закрываю", session_id)
            await self._close_quietly(session_id)
            return
        self._arm(session_id, aware(row.ends_at))

    async def _close_quietly(self, session_id: uuid.UUID) -> None:
        """Закрывает сессию как остановленную, без сообщений в чат."""
        async with self._gate:
            async with self._session_factory() as session:
                async with session.begin():
                    await GameRepo(session).close(session_id, "stopped")
        self._forget(session_id)

    async def _abandon(self, session_id: uuid.UUID, chat_id: int, exc: Exception) -> None:
        """Чат недоступен: закрыть сессию и пометить чат неактивным."""
        logger.warning("Чат %s недоступен, сессия %s закрывается: %s", chat_id, session_id, exc)
        await self._close_quietly(session_id)
        await self._deactivate(chat_id)

    async def _deactivate(self, chat_id: int) -> None:
        async with self._gate:
            async with self._session_factory() as session:
                async with session.begin():
                    await ChatRepo(session).set_active(chat_id, False)

    def _arm(self, session_id: uuid.UUID, deadline: datetime) -> None:
        """Взводит таймер до дедлайна фазы; повторный вызов заменяет таймер."""
        self._cancel_timer(session_id)
        delay = max((deadline - self._now()).total_seconds(), 0)
        task = asyncio.create_task(self._timer(session_id, delay))
        self._timers[session_id] = task

    async def _timer(self, session_id: uuid.UUID, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.advance(session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Ошибка шага не должна silently убивать сессию: логируем,
            # состояние в БД подскажет восстановлению, что делать.
            logger.exception("Таймер сессии %s упал", session_id)

    def _cancel_timer(self, session_id: uuid.UUID) -> None:
        task = self._timers.pop(session_id, None)
        if task is not None:
            task.cancel()

    def _lock_for(self, session_id: uuid.UUID) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def _forget(self, session_id: uuid.UUID) -> None:
        """Сессия закончилась: снимаем таймер и блокировку шагов."""
        self._cancel_timer(session_id)
        self._locks.pop(session_id, None)
