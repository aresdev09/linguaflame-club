"""Настройка группы: события бота в чате, /setup и /settings."""

import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.locales import t
from bot.sender import Sender
from bot.services.group_lifecycle import (
    migrate_chat,
    register_bot_added,
    register_bot_removed,
)

logger = logging.getLogger(__name__)

router = Router(name="group_admin")

# Команды и события этого роутера работают только в группах.
GROUP_CHAT_TYPES = {"group", "supergroup"}
router.message.filter(F.chat.type.in_(GROUP_CHAT_TYPES))
router.my_chat_member.filter(F.chat.type.in_(GROUP_CHAT_TYPES))

# Статусы бота, при которых он «в чате».
MEMBER_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
# Статусы, из которых возвращение в чат считается добавлением.
OUTSIDE_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


@router.my_chat_member()
async def bot_membership_changed(
    update: ChatMemberUpdated,
    session_factory: async_sessionmaker[AsyncSession],
    sender: Sender,
) -> None:
    """Бот добавлен, удалён или снова добавлен в группу."""
    chat = update.chat
    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status

    if new_status in OUTSIDE_STATUSES:
        await register_bot_removed(session_factory, chat_id=chat.id)
        return

    was_outside = old_status in OUTSIDE_STATUSES
    if new_status in MEMBER_STATUSES and was_outside:
        chat_row = await register_bot_added(
            session_factory,
            chat_id=chat.id,
            title=chat.title,
            chat_type=chat.type,
        )
        await sender.send(chat.id, t("welcome.added", chat_row.ui_language))


@router.message(F.migrate_to_chat_id)
async def on_group_upgraded(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Группа стала супергруппой: переносим данные на новый chat_id."""
    new_chat_id = message.migrate_to_chat_id
    if new_chat_id is None:  # защита от служебного сообщения без миграции
        return
    await migrate_chat(
        session_factory, old_chat_id=message.chat.id, new_chat_id=new_chat_id
    )


@router.message(F.migrate_from_chat_id)
async def on_group_upgrade_seen_in_supergroup(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Второе служебное сообщение о миграции: приходит уже в супергруппе.

    Обработчик идемпотентен: если перенос уже сделан первым сообщением,
    повторный вызов ничего не меняет.
    """
    old_chat_id = message.migrate_from_chat_id
    if old_chat_id is None:
        return
    await migrate_chat(
        session_factory, old_chat_id=old_chat_id, new_chat_id=message.chat.id
    )
