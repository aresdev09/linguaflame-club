"""Контекст группы: чат и участник в данных хендлера.

Работает только с сообщениями и кнопками в группах: личка, каналы и
служебные события проходят дальше без изменений. Хендлеры получают
готовые строки `chat` (настройки группы) и `member` (участник).
"""

import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Chat, Update, User
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import GROUP_ANONYMOUS_BOT_ID
from bot.models import Chat as ChatRow
from bot.models import Member
from bot.repositories import ChatRepo, MemberRepo

logger = logging.getLogger(__name__)

GROUP_CHAT_TYPES = {"group", "supergroup"}


class GroupContextMiddleware(BaseMiddleware):
    """Подгружает (или создаёт) чат и участника для действий в группе."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(self, handler: Any, event: Update, data: dict[str, Any]) -> Any:
        chat = _event_chat(event)
        if chat is None or chat.type not in GROUP_CHAT_TYPES:
            return await handler(event, data)

        # Служебные сообщения о миграции в супергруппу: chat_id в базе
        # ещё (или уже) другой — контекст загружать нельзя.
        if _is_migration_message(event):
            return await handler(event, data)

        chat_row, member = await self._load(chat, _event_user(event))
        data["chat"] = chat_row
        data["lang"] = chat_row.ui_language
        if member is not None:
            data["member"] = member
        return await handler(event, data)

    async def _load(self, chat: Chat, user: User | None) -> tuple[ChatRow, Member | None]:
        """Читает или создаёт строки чата и участника в короткой транзакции."""
        for attempt in (1, 2):
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        chat_row = await ChatRepo(session).get_or_create(
                            chat.id, title=chat.title, chat_type=chat.type
                        )
                        member = None
                        if _track_user(user):
                            member = await MemberRepo(session).get_or_create(
                                chat.id,
                                user.id,
                                display_name=user.full_name,
                                username=user.username,
                            )
                return chat_row, member
            except IntegrityError:
                # Два апдейта одновременно создали чат или участника: читаем.
                if attempt == 2:
                    raise
                logger.debug("Гонка создания чата/участника, повтор для %s", chat.id)
        raise AssertionError("цикл повторов завершился без результата")  # pragma: no cover


def _event_chat(event: Update) -> Chat | None:
    if event.message is not None:
        return event.message.chat
    if event.callback_query is not None and event.callback_query.message is not None:
        return event.callback_query.message.chat
    return None


def _event_user(event: Update) -> User | None:
    if event.message is not None:
        return event.message.from_user
    if event.callback_query is not None:
        return event.callback_query.from_user
    return None


def _is_migration_message(event: Update) -> bool:
    message = event.message
    if message is None:
        return False
    return message.migrate_to_chat_id is not None or message.migrate_from_chat_id is not None


def _track_user(user: User | None) -> bool:
    """Участника пишем только для обычных пользователей.

    Анонимные администраторы пишут от служебного GroupAnonymousBot —
    в списке участников им места нет.
    """
    return user is not None and user.id != GROUP_ANONYMOUS_BOT_ID
