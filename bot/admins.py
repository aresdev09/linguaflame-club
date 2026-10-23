"""Проверка прав администратора группы с кешем."""

import logging
from time import monotonic

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

# Служебный аккаунт «GroupAnonymousBot»: от его имени пишут анонимные админы.
GROUP_ANONYMOUS_BOT_ID = 1087968824

# Статусы, при которых пользователь может настраивать группу.
# Кортеж, а не множество: сравнение по значению работает и со строкой.
ADMIN_STATUSES: tuple[str, ...] = (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)


class AdminCache:
    """Кеш результатов `get_chat_member`: не дёргать Telegram на каждую команду."""

    def __init__(self, bot: Bot, ttl_seconds: float = 60.0) -> None:
        self._bot = bot
        self._ttl = ttl_seconds
        self._cache: dict[tuple[int, int], tuple[bool, float]] = {}

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        """True, если пользователь — создатель или администратор чата."""
        cached = self._cache.get((chat_id, user_id))
        if cached is not None and cached[1] > monotonic():
            return cached[0]
        try:
            member = await self._bot.get_chat_member(chat_id, user_id)
            allowed = member.status in ADMIN_STATUSES
        except TelegramBadRequest:
            # «user not found», «member not found» и подобное — не админ.
            allowed = False
        self._cache[(chat_id, user_id)] = (allowed, monotonic() + self._ttl)
        return allowed


def is_anonymous_admin(message: Message | None) -> bool:
    """True, если сообщение отправлено анонимным администратором.

    Анонимные админы пишут от служебного аккаунта GroupAnonymousBot
    или публикуются от имени самой группы (`sender_chat`).
    """
    if message is None or message.from_user is None:
        return False
    if message.from_user.id == GROUP_ANONYMOUS_BOT_ID:
        return True
    return message.sender_chat is not None and message.sender_chat.id == message.chat.id


async def check_admin(event: Message | CallbackQuery, cache: AdminCache) -> bool:
    """Проверяет права отправителя команды или нажатия кнопки.

    Анонимные администраторы считаются админами; для обычных
    пользователей работает кеш.
    """
    message = event if isinstance(event, Message) else event.message
    if is_anonymous_admin(message):
        return True
    user = event.from_user
    if user is None or message is None:
        return False
    return await cache.is_admin(message.chat.id, user.id)
