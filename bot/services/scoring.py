"""Очки и рейтинг: начисление очков в журнал `score_events`.

Очки никогда не хранятся счётчиками: единственный источник правды —
журнал, а рейтинг считается агрегатом запроса. Дневной лимит считается
по календарному дню часового пояса чата.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import utcnow
from bot.repositories import ChatRepo, ScoreRepo

logger = logging.getLogger(__name__)


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


class ScoringService:
    """Начисляет очки через журнал `score_events`."""

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
