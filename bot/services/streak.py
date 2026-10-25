"""Огонёк группы: серия дней с активностью.

Дни считаются по часовому поясу чата (`chats.timezone`). Активность
в тот же день не наращивает серию дважды, пропущенный день — обнуляет
до единицы. Хранит и считает `StreakRepo`, здесь только правило.

Днём активности считается день, в котором хотя бы один участник получил
очки, поэтому `register_activity` вызывается при каждом начислении
(`ScoringService`) и при закрытии игры.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.locales import plural_days as days_text
from bot.locales import t
from bot.models import utcnow
from bot.repositories import StreakRepo
from bot.services.scoring import resolve_timezone

logger = logging.getLogger(__name__)

# Дни, на которых огонёк празднуется.
MILESTONES: tuple[int, ...] = (3, 7, 14, 30, 100)


def local_date(timezone_name: str, now: datetime) -> date:
    """Дата `now` в часовом поясе чата; неизвестный пояс — UTC."""
    return now.astimezone(resolve_timezone(timezone_name)).date()


def is_milestone(days: int) -> bool:
    """True, если длина серии — праздничная веха."""
    return days in MILESTONES





@dataclass(frozen=True)
class StreakState:
    """Состояние огонька для `/streak` и планировщика."""

    # Действующая серия: 0, если день был пропущен.
    current: int
    best: int
    last_active_date: date | None
    active_today: bool

    @property
    def at_risk(self) -> bool:
        """True, если огонёк горит, но сегодня активности ещё не было."""
        return self.current > 0 and not self.active_today


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

    async def register_activity(self, chat_id: int, timezone_name: str) -> int:
        """Отмечает день активности; возвращает длину текущей серии."""
        async with self._session_factory() as session:
            async with session.begin():
                return await self.register_activity_locked(session, chat_id, timezone_name)

    async def register_activity_locked(
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

    async def state(self, chat_id: int, timezone_name: str) -> StreakState:
        """Состояние огонька: серия, рекорд и была ли активность сегодня.

        Серия из базы может быть устаревшей (день пропущен, а активности
        после этого не было) — такие серии показываются как погасшие.
        """
        today = local_date(timezone_name, self._now())
        async with self._session_factory() as session:
            streak = await StreakRepo(session).get(chat_id)
        if streak is None:
            return StreakState(current=0, best=0, last_active_date=None, active_today=False)
        last = streak.last_active_date
        alive = last is not None and last in (today, today - timedelta(days=1))
        best = streak.best_streak
        if alive:
            best = max(best, streak.current_streak)
        return StreakState(
            current=streak.current_streak if alive else 0,
            best=best,
            last_active_date=last,
            active_today=last == today,
        )

    async def best_streak(self, chat_id: int) -> int:
        """Рекорд огонька группы."""
        async with self._session_factory() as session:
            streak = await StreakRepo(session).get(chat_id)
        return streak.best_streak if streak is not None else 0

    async def reset_broken(self, chat_id: int, timezone_name: str) -> int:
        """Обнуляет погасший огонёк; возвращает потерянную длину серии.

        Погасшим считается огонёк, у которого последний активный день
        был позавчера или раньше. Рекорд (`best_streak`) сохраняется.
        Вызывается планировщиком утром после пропущенного дня.
        """
        today = local_date(timezone_name, self._now())
        async with self._session_factory() as session:
            async with session.begin():
                streak = await StreakRepo(session).get_or_create(chat_id)
                last = streak.last_active_date
                if last is None or last >= today - timedelta(days=1):
                    return 0
                lost = streak.current_streak
                await StreakRepo(session).set_progress(
                    chat_id,
                    current_streak=0,
                    best_streak=max(streak.best_streak, lost),
                    last_active_date=last,
                )
        return lost


def format_streak(state: StreakState, lang: str) -> str:
    """Текст /streak: текущий огонёк, рекорд и активность сегодня."""
    lines = [t("streak.title", lang)]
    if state.current <= 0:
        lines.append(t("streak.out", lang))
    else:
        lines.append(t("streak.current", lang, days=days_text(state.current, lang)))
    lines.append(t("streak.best", lang, days=days_text(state.best, lang)))
    key = "streak.today_active" if state.active_today else "streak.today_pending"
    lines.append(t(key, lang))
    return "\n".join(lines)
