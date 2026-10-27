"""Кулдауны на команды: антиспам без лишних сообщений.

Лишние команды молча отбрасываются: обычный лимит — на пользователя,
тяжёлые команды (ИИ, игры) — дополнительно на весь чат. Счётчики живут
в памяти процесса: при нескольких инстансах лимит считается в рамках
каждого инстанса.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, Update

logger = logging.getLogger(__name__)

# Команды, которые дёргают ИИ или запускают игры: общий кулдаун на чат.
HEAVY_COMMANDS = {
    "quiz",
    "scramble",
    "gap",
    "race",
    "truefalse",
    "word",
    "grammar",
}

_PRUNE_THRESHOLD = 1024
_MAX_IDLE_SECONDS = 3600.0


class CooldownTracker:
    """Простое правило: не чаще одного действия на ключ за N секунд."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._last: dict[Any, float] = {}

    def allow(self, key: Any, seconds: float) -> bool:
        """True, если действие можно выполнить сейчас; отмечает время."""
        now = self._monotonic()
        previous = self._last.get(key)
        if previous is not None and now - previous < seconds:
            return False
        self._last[key] = now
        if len(self._last) > _PRUNE_THRESHOLD:
            self._last = {
                item: seen for item, seen in self._last.items() if now - seen < _MAX_IDLE_SECONDS
            }
        return True


class ThrottleMiddleware(BaseMiddleware):
    """Ограничивает частоту команд в группах.

    Обычные команды — не чаще, чем раз в `user_cooldown` секунд на
    пользователя; тяжёлые — дополнительно не чаще, чем раз в
    `heavy_cooldown` секунд на чат.
    """

    def __init__(self, user_cooldown: float, heavy_cooldown: float) -> None:
        self._user_cooldown = user_cooldown
        self._heavy_cooldown = heavy_cooldown
        self._commands = CooldownTracker()
        self._heavy = CooldownTracker()

    async def __call__(self, handler: Any, event: Update, data: dict[str, Any]) -> Any:
        message = event.message
        command = _command_of(message)
        # Контекст группы (data["chat"]) появляется после GroupContextMiddleware:
        # для лички и служебных событий кулдаунов нет.
        if command is not None and data.get("chat") is not None and message is not None:
            chat_id = message.chat.id
            user_id = message.from_user.id if message.from_user is not None else 0
            if not self._commands.allow((chat_id, user_id), self._user_cooldown):
                logger.debug("Кулдаун: слишком частая команда /%s от %s", command, user_id)
                return None
            if command in HEAVY_COMMANDS and not self._heavy.allow(chat_id, self._heavy_cooldown):
                logger.debug("Кулдаун: тяжёлая команда /%s в чате %s", command, chat_id)
                return None
        return await handler(event, data)


def _command_of(message: Message | None) -> str | None:
    """Команда из текста сообщения: /quiz@Bot → quiz."""
    if message is None or message.text is None or not message.text.startswith("/"):
        return None
    command = message.text[1:].split(maxsplit=1)[0].split("@", 1)[0]
    return command.lower() or None
