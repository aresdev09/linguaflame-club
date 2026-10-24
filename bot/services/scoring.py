"""Очки и рейтинг: начисление в журнал, недельные окна, форматирование.

Очки никогда не хранятся счётчиками: единственный источник правды —
журнал `score_events`, а рейтинг считается агрегатом запроса. Неделя и
день считаются по часовому поясу чата, сбрасывать счётчики не нужно.
"""

import html
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.locales import t
from bot.models import Chat, utcnow
from bot.repositories import ChatRepo, GameRepo, LeaderboardEntry, ScoreRepo

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
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._now = now

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
        if points <= 0:
            return 0
        # Строка чата блокируется: параллельные начисления одному участнику
        # не должны вместе перешагнуть дневной лимит.
        async with self._session_factory() as session:
            async with session.begin():
                chat = await ChatRepo(session).get(chat_id, for_update=True)
                timezone_name = chat.timezone if chat is not None else "UTC"
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
        if awarded < points:
            logger.info(
                "Дневной лимит очков: %s из %s для %s в чате %s",
                awarded,
                points,
                user_id,
                chat_id,
            )
        return awarded

    async def top(self, chat: Chat, period: str = PERIOD_WEEK) -> list[LeaderboardEntry]:
        """Топ участников чата за период (`week` или `all`)."""
        since, until = self._period_bounds(chat.timezone, period)
        async with self._session_factory() as session:
            return await ScoreRepo(session).leaderboard(
                chat.id,
                since=since,
                until=until,
                limit=self._settings.leaderboard_top_limit,
            )

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
