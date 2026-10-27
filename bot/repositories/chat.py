"""Репозиторий чатов (групп) и их настроек."""

from sqlalchemy import select, update

from bot.models import (
    AIUsage,
    Chat,
    ChatContentHistory,
    DailyPost,
    GameSession,
    GroupStreak,
    Member,
    ScoreEvent,
    utcnow,
)
from bot.repositories.base import BaseRepo


class ChatRepo(BaseRepo):
    """Доступ к таблице `chats`."""

    async def get(self, chat_id: int, *, for_update: bool = False) -> Chat | None:
        """Читает чат; `for_update` блокирует строку до конца транзакции.

        Блокировка нужна там, где по текущему состоянию гонок (например,
        дневной лимит очков в ScoringService).
        """
        return await self.session.get(Chat, chat_id, with_for_update=for_update)

    async def get_or_create(
        self,
        chat_id: int,
        title: str | None = None,
        chat_type: str = "group",
    ) -> Chat:
        """Возвращает чат, создавая его с настройками по умолчанию."""
        chat = await self.get(chat_id)
        if chat is None:
            chat = Chat(id=chat_id, title=title, type=chat_type)
            self.session.add(chat)
            await self.session.flush()
            return chat
        # Название группы могло измениться.
        if title is not None and chat.title != title:
            chat.title = title
        return chat

    async def list_active(self, *, limit: int = 1000) -> list[Chat]:
        """Активные чаты для планировщика: из неактивных автосообщения не шлём."""
        stmt = select(Chat).where(Chat.active.is_(True)).order_by(Chat.id).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_active(self, chat_id: int, active: bool) -> None:
        """Помечает чат активным или неактивным (бота добавили или удалили)."""
        chat = await self.get(chat_id)
        if chat is None:
            return
        chat.active = active
        chat.deactivated_at = None if active else utcnow()
        await self.session.flush()

    async def update_settings(self, chat_id: int, **values: object) -> None:
        """Обновляет настройки чата; неизвестные поля — ошибка разработчика."""
        chat = await self.get(chat_id)
        if chat is None:
            raise ValueError(f"чат {chat_id} не найден")
        unknown = set(values) - set(Chat.__table__.columns.keys())
        if unknown:
            raise ValueError(f"неизвестные поля настроек: {sorted(unknown)}")
        for field, value in values.items():
            setattr(chat, field, value)
        await self.session.flush()

    async def migrate_chat_id(self, old_chat_id: int, new_chat_id: int) -> None:
        """Переносит все данные чата на новый id (миграция в супергруппу).

        Выполняется в транзакции вызывающего кода. Сначала меняется строка
        чата: в PostgreSQL связанные строки обновляются каскадом
        (`ON UPDATE CASCADE`), затем добираются строки, где каскад не
        сработал (например, SQLite без включённой проверки ключей).
        """
        await self.session.execute(
            update(Chat).where(Chat.id == old_chat_id).values(id=new_chat_id)
        )
        for model in (
            Member,
            ScoreEvent,
            GameSession,
            GroupStreak,
            DailyPost,
            ChatContentHistory,
            AIUsage,
        ):
            await self.session.execute(
                update(model).where(model.chat_id == old_chat_id).values(chat_id=new_chat_id)
            )
        await self.session.flush()
