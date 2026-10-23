"""Жизненный цикл бота в группе: добавление, удаление, миграция в супергруппу."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import Chat
from bot.repositories import ChatRepo

logger = logging.getLogger(__name__)


async def register_bot_added(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chat_id: int,
    title: str | None,
    chat_type: str,
) -> Chat:
    """Бот добавлен: создаёт чат с настройками по умолчанию или включает прежний.

    Повторное добавление настройки и рейтинг не теряет: строка чата уже
    существует, меняется только флаг активности.
    """
    async with session_factory() as session:
        async with session.begin():
            repo = ChatRepo(session)
            chat = await repo.get_or_create(chat_id, title=title, chat_type=chat_type)
            if not chat.active:
                await repo.set_active(chat_id, True)
                logger.info("Чат %s снова активен (бота вернули)", chat_id)
    return chat


async def register_bot_removed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    chat_id: int,
) -> None:
    """Бот удалён: чат помечается неактивным, данные сразу не удаляются."""
    async with session_factory() as session:
        async with session.begin():
            await ChatRepo(session).set_active(chat_id, False)
    logger.info("Чат %s помечен неактивным (бота удалили)", chat_id)


async def migrate_chat(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    old_chat_id: int,
    new_chat_id: int,
) -> None:
    """Миграция группы в супергруппу: chat_id меняется во всех таблицах.

    Идемпотентно: второе служебное сообщение о миграции ничего не делает.
    """
    async with session_factory() as session:
        async with session.begin():
            await ChatRepo(session).migrate_chat_id(old_chat_id, new_chat_id)
    logger.info("Чат %s переехал в супергруппу %s", old_chat_id, new_chat_id)
