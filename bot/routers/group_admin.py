"""Настройка группы: события бота в чате, /setup и /settings."""

import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.locales import t
from bot.sender import Sender
from bot.services.group_lifecycle import register_bot_added, register_bot_removed

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
