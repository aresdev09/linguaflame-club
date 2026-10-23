"""Репозиторий огонька группы."""

from datetime import date

from bot.models import GroupStreak
from bot.repositories.base import BaseRepo


class StreakRepo(BaseRepo):
    """Доступ к таблице `group_streaks`."""

    async def get(self, chat_id: int) -> GroupStreak | None:
        return await self.session.get(GroupStreak, chat_id)

    async def get_or_create(self, chat_id: int) -> GroupStreak:
        streak = await self.get(chat_id)
        if streak is None:
            streak = GroupStreak(chat_id=chat_id, current_streak=0, best_streak=0)
            self.session.add(streak)
            await self.session.flush()
        return streak

    async def set_progress(
        self,
        chat_id: int,
        *,
        current_streak: int,
        best_streak: int,
        last_active_date: date,
    ) -> None:
        """Записывает состояние огонька (расчёт делает StreakService)."""
        streak = await self.get_or_create(chat_id)
        streak.current_streak = current_streak
        streak.best_streak = best_streak
        streak.last_active_date = last_active_date
        await self.session.flush()
