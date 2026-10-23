"""Middleware: необработанные ошибки не роняют бота.

Ошибка уходит в лог; в группу — короткое понятное сообщение на языке
чата. Если Telegram говорит, что чат недоступен (бота удалили, нет
прав), чат помечается неактивным: повторять отправку бессмысленно.
"""

import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import InaccessibleMessage, Message, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.locales import DEFAULT_LANGUAGE, t
from bot.repositories import ChatRepo
from bot.sender import ChatUnavailable, Sender

logger = logging.getLogger(__name__)


class ErrorMiddleware(BaseMiddleware):
    """Ловит все ошибки обработки апдейтов."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sender: Sender,
    ) -> None:
        self._session_factory = session_factory
        self._sender = sender

    async def __call__(self, handler: Any, event: Update, data: dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        except ChatUnavailable as exc:
            logger.warning("Чат недоступен: %s", exc)
            await self._deactivate(_event_chat_id(event))
            return None
        except Exception:
            logger.exception("Необработанная ошибка при обработке апдейта")
            await self._notify(event)
            return None

    async def _notify(self, event: Update) -> None:
        """Короткое сообщение в группу; подробности — только в лог."""
        chat_id = _event_chat_id(event)
        if chat_id is None:
            return
        lang = await self._chat_language(chat_id)
        try:
            await self._sender.send(
                chat_id, t("common.error", lang), thread_id=_event_thread_id(event)
            )
        except ChatUnavailable:
            await self._deactivate(chat_id)
        except Exception:
            logger.exception("Не удалось сообщить об ошибке в чат %s", chat_id)

    async def _chat_language(self, chat_id: int) -> str:
        try:
            async with self._session_factory() as session:
                chat = await ChatRepo(session).get(chat_id)
        except Exception:
            logger.exception("Не удалось прочитать язык чата %s", chat_id)
            return DEFAULT_LANGUAGE
        return chat.ui_language if chat is not None else DEFAULT_LANGUAGE

    async def _deactivate(self, chat_id: int | None) -> None:
        if chat_id is None:
            return
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await ChatRepo(session).set_active(chat_id, False)
            logger.info("Чат %s помечен неактивным: отправка невозможна", chat_id)
        except Exception:
            logger.exception("Не удалось деактивировать чат %s", chat_id)


def _event_message(event: Update) -> Message | InaccessibleMessage | None:
    if event.message is not None:
        return event.message
    if event.callback_query is not None:
        return event.callback_query.message
    return None


def _event_chat_id(event: Update) -> int | None:
    message = _event_message(event)
    return message.chat.id if message is not None else None


def _event_thread_id(event: Update) -> int | None:
    message = _event_message(event)
    return message.message_thread_id if message is not None else None
