"""Очки и рейтинг: начисление в журнал, недельные окна, форматирование.

Очки никогда не хранятся счётчиками: единственный источник правды —
журнал `score_events`, а рейтинг считается агрегатом запроса. Неделя и
день считаются по часовому поясу чата, сбрасывать счётчики не нужно.
"""

from __future__ import annotations

import html
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from enum import Enum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.locales import plural_days, t
from bot.models import Chat, utcnow
from bot.repositories import (
    AccuracyEntry,
    ChatRepo,
    GameRepo,
    LeaderboardEntry,
    ScoreRepo,
)

if TYPE_CHECKING:  # огонёк импортирует этот модуль: связь только для типов
    from bot.services.streak import StreakService

logger = logging.getLogger(__name__)

# Периоды рейтинга для /top.
PERIOD_WEEK = "week"
PERIOD_ALL = "all"

# Источники, которые считаются «игрой» для строки «Лучшая игра».
GAME_SOURCES: tuple[str, ...] = ("quiz", "scramble", "gap", "race", "truefalse")

# Медали для первых трёх мест, дальше номера.
MEDALS: tuple[str, ...] = ("🥇", "🥈", "🥉")

# Имя в рейтинге обрезается до этой длины (после удаления упоминаний).
NAME_MAX_LENGTH = 20

# Сколько мест показывает /top и недельный итог.
RECAP_TOP_LIMIT = 3
# Минимум ответов за неделю, чтобы попасть в «Лучший по точности».
RECAP_MIN_ANSWERS = 5


def resolve_timezone(name: str) -> ZoneInfo:
    """Часовой пояс чата; неизвестное имя — UTC."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Неизвестный часовой пояс %r, считаю по UTC", name)
        return ZoneInfo("UTC")


def day_bounds(timezone_name: str, now: datetime) -> tuple[datetime, datetime]:
    """Границы календарного дня чата в UTC: [начало, конец)."""
    tz = resolve_timezone(timezone_name)
    local_now = now.astimezone(tz)
    start = datetime.combine(local_now.date(), dt_time.min, tzinfo=tz)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def week_start(timezone_name: str, now: datetime) -> datetime:
    """Начало текущей недели чата (понедельник 00:00) в UTC."""
    tz = resolve_timezone(timezone_name)
    local_now = now.astimezone(tz)
    monday = local_now.date() - timedelta(days=local_now.weekday())
    start = datetime.combine(monday, dt_time.min, tzinfo=tz)
    return start.astimezone(UTC)


def previous_week_bounds(
    timezone_name: str, now: datetime
) -> tuple[datetime, datetime]:
    """Границы прошлой недели чата в UTC: [начало, конец).

    Нужны итогам недели: в понедельник утром подводится итог недели,
    которая уже закончилась.
    """
    current_start = week_start(timezone_name, now)
    tz = resolve_timezone(timezone_name)
    previous = (current_start.astimezone(tz) - timedelta(days=7)).astimezone(UTC)
    return previous, current_start


class DailyAward(Enum):
    """Итог начисления, которое даётся один раз в день («Got it»)."""

    AWARDED = "awarded"
    ALREADY = "already"  # очки за сегодня уже получены
    CAPPED = "capped"  # дневной лимит очков не пустил начисление


@dataclass(frozen=True)
class MemberStats:
    """Статистика участника для /me."""

    week_points: int
    total_points: int
    # Место в недельном рейтинге; None — за неделю очков нет.
    place: int | None
    answers: int
    correct_answers: int
    best_game: str | None

    @property
    def accuracy(self) -> int:
        """Процент верных ответов, целое число."""
        if self.answers <= 0:
            return 0
        return round(self.correct_answers * 100 / self.answers)


class ScoringService:
    """Начисляет очки через журнал и читает агрегаты рейтинга."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        now: Callable[[], datetime] = utcnow,
        streak: StreakService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._now = now
        # Огонёк обновляется при каждом начислении очков (см. StreakService).
        self._streak = streak

    async def award(
        self,
        chat_id: int,
        user_id: int,
        points: int,
        source: str,
        session_id: uuid.UUID | None = None,
    ) -> int:
        """Начисляет очки с дневным лимитом; возвращает начисленное.

        Дневной лимит (`score_daily_cap`, 0 — без лимита) считается по
        календарному дню часового пояса чата. Если лимит исчерпан, событие
        не записывается вовсе.
        """
        # Строка чата блокируется: параллельные начисления одному участнику
        # не должны вместе перешагнуть дневной лимит.
        async with self._session_factory() as session:
            async with session.begin():
                chat = await ChatRepo(session).get(chat_id, for_update=True)
                timezone_name = chat.timezone if chat is not None else "UTC"
                return await self.award_locked(
                    session,
                    timezone_name,
                    chat_id,
                    user_id,
                    points,
                    source,
                    session_id=session_id,
                )

    async def award_locked(
        self,
        session: AsyncSession,
        timezone_name: str,
        chat_id: int,
        user_id: int,
        points: int,
        source: str,
        session_id: uuid.UUID | None = None,
    ) -> int:
        """То же начисление внутри готовой транзакции вызывающего кода.

        Блокировки строк держит вызывающий: игры записывают ответ и очки
        одной транзакцией — без окна, в котором ответ есть, а очков нет
        (или наоборот). Часовой пояс передаётся снаружи, чтобы не читать
        чат повторно под уже взятыми блокировками.
        """
        if points <= 0:
            return 0
        day_start, day_end = day_bounds(timezone_name, self._now())
        earned = await ScoreRepo(session).total(
            chat_id, user_id, since=day_start, until=day_end
        )
        awarded = points
        cap = self._settings.score_daily_cap
        if cap > 0:
            awarded = min(points, max(cap - earned, 0))
        if awarded > 0:
            await ScoreRepo(session).add_event(
                chat_id,
                user_id,
                points=awarded,
                source=source,
                session_id=session_id,
            )
            await self._register_activity(session, chat_id, timezone_name)
        if awarded < points:
            logger.info(
                "Дневной лимит очков: %s из %s для %s в чате %s",
                awarded,
                points,
                user_id,
                chat_id,
            )
        return awarded

    async def award_once_per_day(
        self,
        chat_id: int,
        user_id: int,
        points: int,
        source: str,
    ) -> DailyAward:
        """Начисляет очки один раз за день участника; повтор — без начисления.

        Проверка и запись идут в одной транзакции под блокировкой строки
        чата: два одновременных нажатия кнопки не могут дать две награды.
        """
        async with self._session_factory() as session:
            async with session.begin():
                chat = await ChatRepo(session).get(chat_id, for_update=True)
                timezone_name = chat.timezone if chat is not None else "UTC"
                day_start, day_end = day_bounds(timezone_name, self._now())
                scores = ScoreRepo(session)
                if await scores.has_event(
                    chat_id, user_id, source=source, since=day_start, until=day_end
                ):
                    return DailyAward.ALREADY
                awarded = await self.award_locked(
                    session, timezone_name, chat_id, user_id, points, source
                )
        return DailyAward.AWARDED if awarded > 0 else DailyAward.CAPPED

    async def _register_activity(
        self, session: AsyncSession, chat_id: int, timezone_name: str
    ) -> None:
        """Отмечает день активности для огонька группы.

        Вызывается при каждом начислении: день считается активным, если
        хотя бы один участник получил очки. Огонёк необязателен (тесты и
        служебные начисления могут работать без него).
        """
        if self._streak is None:
            return
        await self._streak.register_activity_locked(session, chat_id, timezone_name)

    async def top(self, chat: Chat, period: str = PERIOD_WEEK) -> list[LeaderboardEntry]:
        """Топ участников чата за период (`week` или `all`)."""
        since, until = self._period_bounds(chat.timezone, period)
        return await self.leaderboard(chat, since=since, until=until)

    async def leaderboard(
        self,
        chat: Chat,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[LeaderboardEntry]:
        """Топ участников за произвольный период — для итогов недели."""
        async with self._session_factory() as session:
            return await ScoreRepo(session).leaderboard(
                chat.id,
                since=since,
                until=until,
                limit=limit or self._settings.leaderboard_top_limit,
            )

    async def best_accuracy(
        self, chat: Chat, *, since: datetime, until: datetime
    ) -> AccuracyEntry | None:
        """Участник с лучшей точностью за период (не меньше 5 ответов)."""
        async with self._session_factory() as session:
            entries = await GameRepo(session).accuracy_entries(
                chat.id,
                since=since,
                until=until,
                min_answers=RECAP_MIN_ANSWERS,
                limit=1,
            )
        return entries[0] if entries else None

    async def member_stats(self, chat: Chat, user_id: int) -> MemberStats:
        """Статистика участника: очки, место, ответы, лучшая игра."""
        week_from = week_start(chat.timezone, self._now())
        async with self._session_factory() as session:
            scores = ScoreRepo(session)
            week_points = await scores.total(chat.id, user_id, since=week_from)
            total_points = await scores.total(chat.id, user_id)
            place = await scores.place(chat.id, user_id, since=week_from)
            best_game = await scores.best_source(chat.id, user_id, sources=GAME_SOURCES)
            answers, correct = await GameRepo(session).answer_stats(chat.id, user_id)
        return MemberStats(
            week_points=week_points,
            total_points=total_points,
            place=place,
            answers=answers,
            correct_answers=correct,
            best_game=best_game,
        )

    def _period_bounds(
        self, timezone_name: str, period: str
    ) -> tuple[datetime | None, datetime | None]:
        if period == PERIOD_ALL:
            return None, None
        return week_start(timezone_name, self._now()), None


def clean_display_name(
    name: str | None,
    user_id: int,
    lang: str,
    *,
    max_length: int = NAME_MAX_LENGTH,
) -> str:
    """Имя для рейтинга: без `@`, обрезанное и безопасное для HTML.

    Отображаемые имена приходят от пользователей: экранирование обязательно,
    упоминания удаляются, чтобы бот не пинговал людей.
    """
    raw = (name or "").strip().replace("@", "").strip()
    if not raw:
        return html.escape(t("top.unknown_player", lang, user_id=user_id))
    if len(raw) > max_length:
        raw = raw[: max_length - 1] + "…"
    return html.escape(raw)


def format_top(entries: list[LeaderboardEntry], lang: str, period: str = PERIOD_WEEK) -> str:
    """Текст /top: медали для первых трёх, номера дальше."""
    title = t(f"top.title_{period}", lang)
    if not entries:
        return f"{title}\n\n{t('top.empty', lang)}"
    lines = [title, ""]
    for index, entry in enumerate(entries, start=1):
        place = MEDALS[index - 1] if index <= len(MEDALS) else f"{index}."
        lines.append(
            t(
                "top.entry",
                lang,
                place=place,
                name=clean_display_name(entry.display_name, entry.user_id, lang),
                points=entry.points,
            )
        )
    return "\n".join(lines)


def format_weekly_recap(
    entries: list[LeaderboardEntry],
    accuracy: AccuracyEntry | None,
    best_streak: int,
    lang: str,
    *,
    top_limit: int = RECAP_TOP_LIMIT,
) -> str:
    """Итоги недели: победитель, топ мест, точность и рекорд огонька.

    Победитель получает только «звание» в сообщении, очки не меняются.
    """
    lines = [t("recap.title", lang), ""]
    winner = entries[0]
    lines.append(
        t(
            "recap.winner",
            lang,
            name=clean_display_name(winner.display_name, winner.user_id, lang),
            points=winner.points,
        )
    )
    lines += ["", t("recap.top", lang)]
    for index, entry in enumerate(entries[:top_limit], start=1):
        place = MEDALS[index - 1] if index <= len(MEDALS) else f"{index}."
        lines.append(
            t(
                "top.entry",
                lang,
                place=place,
                name=clean_display_name(entry.display_name, entry.user_id, lang),
                points=entry.points,
            )
        )
    if accuracy is not None:
        lines += [
            "",
            t(
                "recap.accuracy",
                lang,
                name=clean_display_name(accuracy.display_name, accuracy.user_id, lang),
                accuracy=accuracy.accuracy,
                answers=accuracy.answers,
            ),
        ]
    if best_streak > 0:
        lines += ["", t("recap.flame_record", lang, days=plural_days(best_streak, lang))]
    return "\n".join(lines)


def format_me(stats: MemberStats, lang: str) -> str:
    """Текст /me: коротко очки, место, ответы и лучшая игра."""
    lines = [
        t("me.title", lang),
        t("me.week_points", lang, points=stats.week_points),
        t("me.all_time_points", lang, points=stats.total_points),
        t("me.place", lang, place=stats.place)
        if stats.place is not None
        else t("me.no_place", lang),
        t("me.answers", lang, answers=stats.answers, accuracy=stats.accuracy),
    ]
    if stats.best_game is not None:
        lines.append(t("me.best_game", lang, game=t(f"game.{stats.best_game}", lang)))
    return "\n".join(lines)
