"""Огонёк группы: серия дней с активностью.

Дни считаются по часовому поясу чата (`chats.timezone`). Активность
в тот же день не наращивает серию дважды, пропущенный день — обнуляет
до единицы. Хранит и считает `StreakRepo`, здесь только правило.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import utcnow
from bot.repositories import StreakRepo
from bot.services.scoring import resolve_timezone

logger = logging.getLogger(__name__)


def local_date(timezone_name: str, now: datetime) -> date:
    """Дата `now` в часовом поясе чата; неизвестный пояс — UTC."""
    return now.astimezone(resolve_timezone(timezone_name)).date()


class StreakService:
    """Считает и обновляет огонёк группы."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._now = now

    async def mark_active(self, chat_id: int, timezone_name: str) -> int:
        """Отмечает день активности; возвращает длину текущей серии."""
        async with self._session_factory() as session:
            async with session.begin():
                return await self.mark_active_locked(session, chat_id, timezone_name)

    async def mark_active_locked(
        self, session: AsyncSession, chat_id: int, timezone_name: str
    ) -> int:
        """То же внутри готовой транзакции вызывающего кода.

        Так итоги игры обновляют огонёк атомарно с закрытием сессии.
        """
        today = local_date(timezone_name, self._now())
        streak = await StreakRepo(session).get_or_create(chat_id)
        if streak.last_active_date == today:
            # Уже отмечались сегодня: серия не растёт повторно.
            current = max(streak.current_streak, 1)
        elif streak.last_active_date == today - timedelta(days=1):
            current = streak.current_streak + 1
        else:
            # Прошлый раз играли давно (или впервые) — серия начинается.
            current = 1
        best = max(streak.best_streak, current)
        await StreakRepo(session).set_progress(
            chat_id,
            current_streak=current,
            best_streak=best,
            last_active_date=today,
        )
        return current
