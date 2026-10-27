"""Репозиторий обслуживания данных: очистка ответов и удаление чатов.

Схема приватности: тексты ответов в играх не хранятся дольше 7 дней, а
данные удалённой из бота группы стираются через 30 дней. Обе операции
живут здесь, чтобы сервис уборки не собирал SQL сам.
"""

from datetime import datetime

from sqlalchemy import delete, select, update

from bot.models import (
    AdminAction,
    AIUsage,
    Chat,
    ChatContentHistory,
    DailyPost,
    GameAnswer,
    GameSession,
    GroupStreak,
    Member,
    ScheduledMessage,
    ScoreEvent,
    ScoreReset,
)
from bot.repositories.base import BaseRepo


class MaintenanceRepo(BaseRepo):
    """Уборка данных: тексты ответов и данные неактивных чатов."""

    async def clear_answer_texts_before(self, cutoff: datetime) -> int:
        """Очищает тексты ответов старше `cutoff`; статистика остаётся."""
        result = await self.session.execute(
            update(GameAnswer)
            .where(GameAnswer.created_at < cutoff, GameAnswer.answer != "")
            .values(answer="")
        )
        return int(result.rowcount or 0)

    async def list_purgeable_chat_ids(self, before: datetime, *, limit: int = 500) -> list[int]:
        """Чаты, удалённые из бота раньше `before`: данные пора стереть."""
        stmt = (
            select(Chat.id)
            .where(
                Chat.active.is_(False),
                Chat.deactivated_at.is_not(None),
                Chat.deactivated_at < before,
            )
            .order_by(Chat.id)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [int(chat_id) for chat_id in result.scalars().all()]

    async def delete_chat_data(self, chat_id: int) -> None:
        """Удаляет все данные чата в правильном порядке (внешние ключи)."""
        session_ids = select(GameSession.id).where(GameSession.chat_id == chat_id)
        await self.session.execute(delete(GameAnswer).where(GameAnswer.session_id.in_(session_ids)))
        await self.session.execute(delete(ScoreEvent).where(ScoreEvent.chat_id == chat_id))
        await self.session.execute(delete(GameSession).where(GameSession.chat_id == chat_id))
        await self.session.execute(delete(ScoreReset).where(ScoreReset.chat_id == chat_id))
        for model in (
            Member,
            GroupStreak,
            DailyPost,
            ScheduledMessage,
            ChatContentHistory,
            AIUsage,
            AdminAction,
        ):
            await self.session.execute(delete(model).where(model.chat_id == chat_id))
        await self.session.execute(delete(Chat).where(Chat.id == chat_id))
        await self.session.flush()
