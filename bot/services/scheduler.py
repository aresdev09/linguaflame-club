"""Планировщик автосообщений: пост дня, огонёк, напоминания и итоги недели.

Одна задача `tick` раз в минуту выбирает активные чаты и смотрит, что
уже наступило по локальному времени группы. Так проще, чем отдельная
задача на каждый чат: часовой пояс группы — обычная настройка, и она
может меняться.

Каждое автосообщение фиксируется в `scheduled_messages`: запись не даёт
отправить одно и то же дважды и считает лимит
`scheduler_messages_per_day` на сутки. Все отправки идут через `Sender`
(лимиты Telegram), ошибки «бота удалили» выключают чат, а `RetryAfter`
переносит сообщение на более позднее время.
"""

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time

from aiogram.types import InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.keyboards import daily_grammar_keyboard, daily_words_keyboard
from bot.locales import plural_days, t
from bot.models import Chat, ScheduledMessage, utcnow
from bot.repositories import ChatRepo, ContentRepo, SchedulerRepo, StreakRepo
from bot.sender import ChatUnavailable, Sender, TelegramRateLimited
from bot.services.daily import (
    KIND_GRAMMAR,
    KIND_WORDS,
    DailyPostResult,
    DailyService,
    format_grammar_post,
    format_words_post,
)
from bot.services.scoring import (
    RECAP_TOP_LIMIT,
    ScoringService,
    format_weekly_recap,
    previous_week_bounds,
    resolve_timezone,
)
from bot.services.streak import StreakService, is_milestone

logger = logging.getLogger(__name__)

# Виды автосообщений в журнале `scheduled_messages`.
JOURNAL_DAILY = "daily"
JOURNAL_REMINDER = "reminder"
JOURNAL_STREAK_LOST = "streak_lost"
JOURNAL_MILESTONE = "milestone"
JOURNAL_RECAP = "recap"

# Итоги недели: понедельник, через 30 минут после поста дня.
RECAP_WEEKDAY = 0
RECAP_DELAY = timedelta(minutes=30)

# Виды контента в банке вопросов для запасного поста дня.
BANK_KIND = {KIND_WORDS: "words", KIND_GRAMMAR: "grammar"}


def as_utc(value: datetime) -> datetime:
    """Время из базы в UTC: SQLite возвращает наивные значения."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class GroupScheduler:
    """Отправляет автосообщения по локальному времени каждой группы."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        sender: Sender,
        daily: DailyService,
        scoring: ScoringService,
        streak: StreakService,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._sender = sender
        self._daily = daily
        self._scoring = scoring
        self._streak = streak
        self._clock = now
        self._offset = settings.scheduler_time_offset
        self._scheduler: AsyncIOScheduler | None = None

    # --- запуск и остановка ---

    def start(self) -> None:
        """Включает поминутную задачу `tick` в текущем цикле событий."""
        if self._scheduler is not None:
            return
        scheduler = AsyncIOScheduler(timezone=UTC)
        scheduler.add_job(
            self.tick,
            "interval",
            seconds=self._settings.scheduler_tick_seconds,
            id="scheduler-tick",
            # Пропущенные запуски не копятся: следующий tick сам увидит,
            # что время поста уже наступило.
            coalesce=True,
            max_instances=1,
            misfire_grace_time=self._settings.scheduler_tick_seconds,
        )
        scheduler.start()
        self._scheduler = scheduler
        logger.info(
            "Планировщик запущен: проверка каждые %s с, сдвиг времени %s",
            self._settings.scheduler_tick_seconds,
            self._offset,
        )

    def shutdown(self) -> None:
        """Останавливает задачу при выходе из бота."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def now(self) -> datetime:
        """«Сейчас» планировщика: системные часы плюс сдвиг из окружения.

        Сдвиг (`SCHEDULER_TIME_OFFSET`) нужен для ручной проверки: он
        переносит решения планировщика вперёд или назад, не меняя
        настройки групп и системное время.
        """
        moment = self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment + self._offset

    # --- проход по чатам ---

    async def tick(self) -> None:
        """Один проход: что положено чатам прямо сейчас."""
        now = self.now()
        for chat in await self._active_chats():
            try:
                await self.process_chat(chat, now)
            except Exception:
                # Сбой в одной группе не должен останавливать остальные.
                logger.exception("Планировщик: сбой в чате %s", chat.id)

    async def process_chat(self, chat: Chat, now: datetime) -> None:
        """Решает, какие автосообщения положены чату в этот момент."""
        local = now.astimezone(resolve_timezone(chat.timezone))
        today = local.date()

        if await self._is_dormant(chat, today):
            # Заброшенная группа: автосообщения ждут следующей активности.
            logger.debug("Чат %s: активности нет, автосообщения приостановлены", chat.id)
        else:
            await self._maybe_daily_post(chat, local, today, now)
            await self._maybe_milestone(chat, today, now)
            await self._maybe_streak_lost(chat, local, today, now)
            await self._maybe_reminder(chat, local, today, now)
        # Итоги недели не считаются спамом: они уходят только за неделю,
        # в которой были очки.
        await self._maybe_recap(chat, local, today, now)

    # --- пост дня ---

    async def _maybe_daily_post(
        self, chat: Chat, local: datetime, today: date, now: datetime
    ) -> None:
        """Публикует слова и грамматику дня, когда наступило время группы."""
        kinds = self._daily_kinds(chat)
        if not kinds:
            return
        target = self._moment(local, today, self._daily_time(chat))
        if not self._is_due(chat, local, target, JOURNAL_DAILY):
            return
        if self._in_quiet(chat, local.time()):
            return

        message = await self._claim(chat.id, today, JOURNAL_DAILY, now)
        if message is None:
            return
        if message.attempts >= self._settings.scheduler_content_attempts:
            # Попытки кончились: запасной контент из банка или пропуск дня.
            await self._fallback_daily_post(chat, message, kinds, now)
            return

        results: list[tuple[str, DailyPostResult]] = []
        for kind in kinds:
            result = await self._daily_result(chat, kind)
            if not result.chunks:
                # Контент не собрался (нет ИИ или исчерпана квота) — позже
                # попробуем снова; огонёк от пропуска не страдает.
                await self._postpone(message, now)
                return
            results.append((kind, result))
        logger.info("Чат %s: отправляю пост дня (%s)", chat.id, ", ".join(kinds))
        await self._send_post(chat, message, results, now)

    async def _send_post(
        self,
        chat: Chat,
        message: ScheduledMessage,
        results: list[tuple[str, DailyPostResult]],
        now: datetime,
    ) -> None:
        """Отправляет пост дня: кнопка дня — на последнем сообщении вида.

        Запись журнала отмечается сразу после первого удачного сообщения:
        пост дня выходит ровно один раз, даже если часть сообщений не
        дошла (например, Telegram попросил подождать).
        """
        posted = False
        for kind, result in results:
            keyboard = self._daily_keyboard(chat, kind, result)
            last_index = len(result.chunks) - 1
            last_message_id: int | None = None
            for index, chunk in enumerate(result.chunks):
                sent = await self._send(
                    chat, chunk, reply_markup=keyboard if index == last_index else None
                )
                if sent is None:
                    return
                last_message_id = sent.message_id
                if not posted:
                    posted = True
                    await self._mark_sent(message.id, sent.message_id, now)
            await self._daily.mark_sent(result.post_id, last_message_id)

    async def _fallback_daily_post(
        self, chat: Chat, message: ScheduledMessage, kinds: list[str], now: datetime
    ) -> None:
        """Запасной пост дня из банка вопросов, когда ИИ так и не ответил.

        Если запасного контента нет, день пропускается: огонёк считается
        по очкам участников и от пропуска не страдает.
        """
        results = []
        for kind in kinds:
            result = await self._bank_daily(chat, kind)
            if result is not None:
                results.append((kind, result))
        if not results:
            logger.warning(
                "Чат %s: запасного контента нет, пост дня пропущен", chat.id
            )
            return
        await self._send_post(chat, message, results, now)

    async def _bank_daily(self, chat: Chat, kind: str) -> DailyPostResult | None:
        """Собирает пост дня из банка вопросов уровня и языка группы."""
        async with self._session_factory() as session:
            async with session.begin():
                repo = ContentRepo(session)
                items = await repo.pick_bank_items(
                    BANK_KIND[kind],
                    chat.level,
                    chat.translation_language,
                    limit=chat.words_count if kind == KIND_WORDS else 1,
                )
                payloads = [item.payload for item in items]
                for item in items:
                    await repo.mark_bank_item_used(item.id)

        if kind == KIND_WORDS:
            if not payloads:
                return None
            payload = {
                "level": chat.level,
                "language": chat.ui_language,
                "words": payloads,
            }
            post_id = await self._daily.save_payload(chat, kind, payload)
            return DailyPostResult(
                format_words_post(
                    chat.level, payloads, chat.ui_language, points=self._daily.bonus_points
                ),
                post_id=post_id,
                reason=None,
            )
        if not payloads:
            return None
        lesson = payloads[0]
        payload = {"level": chat.level, "language": chat.ui_language, "lesson": lesson}
        post_id = await self._daily.save_payload(chat, kind, payload)
        return DailyPostResult(
            [format_grammar_post(chat.level, lesson, chat.ui_language)],
            post_id=post_id,
            reason=None,
        )

    async def _daily_result(self, chat: Chat, kind: str) -> DailyPostResult:
        """Слова или грамматика дня: сохранённый пост, кеш или ИИ."""
        if kind == KIND_WORDS:
            return await self._daily.words(chat)
        return await self._daily.grammar(chat)

    # --- огонёк группы ---

    async def _maybe_milestone(self, chat: Chat, today: date, now: datetime) -> None:
        """Празднует вехи огонька: 3, 7, 14, 30 и 100 дней."""
        state = await self._streak.state(chat.id, chat.timezone)
        if not state.active_today or not is_milestone(state.current):
            return
        message = await self._claim(chat.id, today, JOURNAL_MILESTONE, now)
        if message is None:
            return
        text = t(
            "streak.milestone",
            chat.ui_language,
            days=plural_days(state.current, chat.ui_language),
        )
        sent = await self._send(chat, text)
        if sent is not None:
            await self._mark_sent(message.id, sent.message_id, now)

    async def _maybe_streak_lost(
        self, chat: Chat, local: datetime, today: date, now: datetime
    ) -> None:
        """Утром сообщает, что огонёк погас, и обнуляет серию."""
        target = self._moment(local, today, self._settings.scheduler_streak_lost_time)
        if not self._is_due(chat, local, target, JOURNAL_STREAK_LOST):
            return
        if self._in_quiet(chat, local.time()):
            return
        lost = await self._streak.reset_broken(chat.id, chat.timezone)
        if lost <= 0:
            return
        message = await self._claim(chat.id, today, JOURNAL_STREAK_LOST, now)
        if message is None:
            return
        text = t(
            "streak.lost",
            chat.ui_language,
            days=plural_days(lost, chat.ui_language),
        )
        sent = await self._send(chat, text)
        if sent is not None:
            await self._mark_sent(message.id, sent.message_id, now)

    async def _maybe_reminder(
        self, chat: Chat, local: datetime, today: date, now: datetime
    ) -> None:
        """Вечером напоминает об огоньке, если сегодня активности не было."""
        target = self._moment(local, today, self._settings.scheduler_reminder_time)
        if not self._is_due(chat, local, target, JOURNAL_REMINDER):
            return
        if self._in_quiet(chat, local.time()):
            return
        state = await self._streak.state(chat.id, chat.timezone)
        if not state.at_risk:
            return
        message = await self._claim(chat.id, today, JOURNAL_REMINDER, now)
        if message is None:
            return
        text = t(
            "streak.reminder",
            chat.ui_language,
            days=plural_days(state.current, chat.ui_language),
        )
        sent = await self._send(chat, text)
        if sent is not None:
            await self._mark_sent(message.id, sent.message_id, now)

    # --- итоги недели ---

    async def _maybe_recap(
        self, chat: Chat, local: datetime, today: date, now: datetime
    ) -> None:
        """По понедельникам публикует итоги прошлой недели."""
        if local.weekday() != RECAP_WEEKDAY:
            return
        target = self._moment(local, today, self._daily_time(chat)) + RECAP_DELAY
        if not self._is_due(chat, local, target, JOURNAL_RECAP):
            return
        if self._in_quiet(chat, local.time()):
            return
        since, until = previous_week_bounds(chat.timezone, now)
        entries = await self._scoring.leaderboard(
            chat, since=since, until=until, limit=RECAP_TOP_LIMIT
        )
        if not entries:
            # За неделю никто не заработал очки — итоги не публикуем.
            return
        message = await self._claim(chat.id, today, JOURNAL_RECAP, now)
        if message is None:
            return
        accuracy = await self._scoring.best_accuracy(chat, since=since, until=until)
        best = await self._streak.best_streak(chat.id)
        text = format_weekly_recap(entries, accuracy, best, chat.ui_language)
        sent = await self._send(chat, text)
        if sent is not None:
            await self._mark_sent(message.id, sent.message_id, now)

    # --- время и расписание ---

    def _daily_time(self, chat: Chat) -> dt_time:
        """Время поста дня с учётом тихих часов.

        Если заданное время попало в тихие часы, пост переносится на
        конец тихих часов — первое разрешённое время суток.
        """
        if chat.quiet_from != chat.quiet_to and self._in_quiet(chat, chat.daily_time):
            return chat.quiet_to
        return chat.daily_time

    @staticmethod
    def _in_quiet(chat: Chat, moment: dt_time) -> bool:
        """True, если время попало в тихие часы группы.

        Окно может идти через полночь (22:00–07:00) или быть выключенным
        (начало совпадает с концом).
        """
        start, end = chat.quiet_from, chat.quiet_to
        if start == end:
            return False
        if start < end:
            return start <= moment < end
        return moment >= start or moment < end

    @staticmethod
    def _moment(local: datetime, on: date, clock: dt_time) -> datetime:
        """Дата и время в часовом поясе чата, для сравнения с «сейчас»."""
        return datetime.combine(on, clock, tzinfo=local.tzinfo)

    def _is_due(
        self, chat: Chat, local: datetime, target: datetime, kind: str
    ) -> bool:
        """Наступило ли время автосообщения с учётом разброса отправки."""
        jitter = timedelta(seconds=self._jitter_seconds(chat.id, local.date(), kind))
        return local >= target + jitter

    def _jitter_seconds(self, chat_id: int, on_date: date, kind: str) -> int:
        """Стабильный разброс отправки: у группы свой сдвиг на каждый день.

        Разброс детерминирован (чат, дата, вид сообщения): повторный tick
        принимает то же решение, а группы не получают автосообщения одной
        секундой.
        """
        limit = self._settings.scheduler_jitter_max_seconds
        if limit <= 0:
            return 0
        digest = hashlib.sha256(f"{chat_id}:{on_date}:{kind}".encode()).digest()
        return int.from_bytes(digest[:4], "big") % (limit + 1)

    def _daily_kinds(self, chat: Chat) -> list[str]:
        """Что публиковать в посте дня по настройкам группы."""
        kinds = []
        if chat.daily_words:
            kinds.append(KIND_WORDS)
        if chat.daily_grammar:
            kinds.append(KIND_GRAMMAR)
        return kinds

    async def _is_dormant(self, chat: Chat, today: date) -> bool:
        """True, если в группе давно не зарабатывали очки.

        Отсчёт идёт от последнего дня с очками, а для групп, где очков не
        было никогда, — от даты добавления бота. Так заброшенные группы
        не получают автосообщения «в пустоту».
        """
        async with self._session_factory() as session:
            streak = await StreakRepo(session).get(chat.id)
        last = streak.last_active_date if streak is not None else None
        if last is None:
            timezone = resolve_timezone(chat.timezone)
            last = as_utc(chat.created_at).astimezone(timezone).date()
        return (today - last).days >= self._settings.scheduler_dormant_days

    # --- журнал автосообщений ---

    async def _claim(
        self, chat_id: int, today: date, kind: str, now: datetime
    ) -> ScheduledMessage | None:
        """Занимает слот автосообщения на день.

        `None` означает, что отправлять нечего: сообщение уже ушло, ждёт
        повтора после сбоя или не влезло в лимит автосообщений на сутки.
        Запись создаётся до отправки, поэтому повторный `tick` дубля не
        сделает. На время отправки запись получает «замок» — время
        следующей попытки; успешная отправка его снимает.
        """
        retry_at = now + timedelta(seconds=self._settings.scheduler_retry_delay_seconds)
        for _ in range(2):
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        repo = SchedulerRepo(session)
                        existing = await repo.get(chat_id, today, kind)
                        if existing is None:
                            limit = self._settings.scheduler_messages_per_day
                            if await repo.count(chat_id, today) >= limit:
                                logger.info(
                                    "Чат %s: лимит %d автосообщений в сутки исчерпан",
                                    chat_id,
                                    limit,
                                )
                                return None
                            return await repo.create(
                                chat_id, today, kind, lease_until=retry_at
                            )
                        if existing.posted_at is not None:
                            return None
                        next_attempt = existing.next_attempt_at
                        if next_attempt is not None and as_utc(next_attempt) > now:
                            return None
                        return existing
            except IntegrityError:
                # Запись создали параллельно: следующая попытка её прочитает.
                logger.debug(
                    "Автосообщение %s для чата %s создано параллельно", kind, chat_id
                )
        return None

    async def _postpone(self, message: ScheduledMessage, now: datetime) -> None:
        """Откладывает автосообщение: контент не собрался, пробуем позже."""
        retry_at = now + timedelta(seconds=self._settings.scheduler_retry_delay_seconds)
        async with self._session_factory() as session:
            async with session.begin():
                attempts = await SchedulerRepo(session).postpone(
                    message.id, next_attempt_at=retry_at
                )
        logger.info(
            "Чат %s: автосообщение %s отложено до %s (попытка %d)",
            message.chat_id,
            message.kind,
            retry_at.isoformat(timespec="seconds"),
            attempts,
        )

    async def _mark_sent(
        self, message_id: int, telegram_message_id: int | None, now: datetime
    ) -> None:
        """Отмечает автосообщение отправленным во времени планировщика."""
        async with self._session_factory() as session:
            async with session.begin():
                await SchedulerRepo(session).mark_sent(
                    message_id, telegram_message_id, at=now
                )

    # --- отправка ---

    async def _send(
        self,
        chat: Chat,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message | None:
        """Отправляет сообщение через Sender; ошибки не роняют планировщик.

        `None` — сообщение не ушло: чат недоступен (он выключается) или
        Telegram просит подождать (попробуем позже).
        """
        try:
            return await self._sender.send(
                chat.id,
                text,
                thread_id=chat.thread_id,
                reply_markup=reply_markup,
            )
        except TelegramRateLimited as exc:
            logger.warning("Telegram просит подождать, чат %s: %s", chat.id, exc)
            return None
        except ChatUnavailable as exc:
            logger.warning("Чат %s недоступен, выключаю автосообщения: %s", chat.id, exc)
            await self._deactivate(chat.id)
            return None

    async def _deactivate(self, chat_id: int) -> None:
        """Помечает чат неактивным: писать в него больше не будем."""
        async with self._session_factory() as session:
            async with session.begin():
                await ChatRepo(session).set_active(chat_id, False)

    def _daily_keyboard(
        self, chat: Chat, kind: str, result: DailyPostResult
    ) -> InlineKeyboardMarkup | None:
        """Кнопка дня под постом; без записи в `daily_posts` кнопки нет."""
        if result.post_id is None:
            return None
        if kind == KIND_WORDS:
            return daily_words_keyboard(result.post_id, chat.ui_language)
        return daily_grammar_keyboard(result.post_id, chat.ui_language)

    async def _active_chats(self) -> list[Chat]:
        """Активные чаты: из неактивных автосообщения не отправляются."""
        async with self._session_factory() as session:
            return await ChatRepo(session).list_active()
