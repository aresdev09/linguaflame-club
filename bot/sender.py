"""Отправка сообщений в Telegram: очередь, лимиты и повторы.

Все исходящие сообщения и правки идут через `Sender`: Telegram ограничивает
группу примерно 20 сообщениями в минуту, а глобально — десятками в секунду.
"""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

# Фрагменты ошибок Telegram, после которых чат считается недоступным.
UNAVAILABLE_MARKERS = (
    "chat not found",
    "bot was kicked",
    "not enough rights",
    "bot was blocked",
    "user is deactivated",
    "chat was upgraded",
)


class ChatUnavailable(RuntimeError):
    """Чат недоступен: бота удалили, нет прав или чат больше не существует."""


class _SlidingWindow:
    """Скользящее окно: не больше `limit` вызовов за `window` секунд."""

    def __init__(self, limit: int, window: float) -> None:
        self._limit = max(limit, 1)
        self._window = max(window, 0.01)
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Ждёт свободный слот в окне и занимает его."""
        async with self._lock:
            while True:
                now = monotonic()
                while self._times and now - self._times[0] >= self._window:
                    self._times.popleft()
                if len(self._times) < self._limit:
                    self._times.append(now)
                    return
                await asyncio.sleep(max(self._window - (now - self._times[0]), 0.001))


class Sender:
    """Единая точка отправки: лимиты на чат и глобальный, повторы, ошибки."""

    def __init__(
        self,
        bot: Bot,
        *,
        per_chat_limit: int = 20,
        per_chat_window: float = 60.0,
        global_limit: int = 20,
        global_window: float = 1.0,
        retry_attempts: int = 3,
    ) -> None:
        self._bot = bot
        self._per_chat_limit = per_chat_limit
        self._per_chat_window = per_chat_window
        self._global = _SlidingWindow(global_limit, global_window)
        self._retry_attempts = max(retry_attempts, 1)
        self._chat_windows: dict[int, _SlidingWindow] = {}

    # --- Публичные методы ---

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_to_message_id: int | None = None,
    ) -> Message | None:
        """Отправляет сообщение; в тему форума — с `thread_id`."""
        return await self._call(
            chat_id,
            lambda: self._bot.send_message(
                chat_id=chat_id,
                text=text,
                message_thread_id=thread_id,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            ),
        )

    async def reply(
        self,
        message: Message,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message | None:
        """Отвечает на сообщение, сохраняя тему форума."""
        return await self.send(
            message.chat.id,
            text,
            thread_id=message.message_thread_id,
            reply_markup=reply_markup,
            reply_to_message_id=message.message_id,
        )

    async def edit(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message | None:
        """Правит сообщение (правки тоже расходуют лимит)."""
        try:
            return await self._call(
                chat_id,
                lambda: self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                ),
            )
        except TelegramBadRequest as exc:
            # Повторное открытие меню без изменений — не ошибка.
            if "message is not modified" in str(exc):
                return None
            raise

    async def answer_callback(
        self,
        callback: CallbackQuery,
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> None:
        """Убирает «часики» на кнопке; ошибки здесь не важны."""
        try:
            await callback.answer(text=text, show_alert=show_alert)
        except Exception:
            logger.debug("Не удалось ответить на callback", exc_info=True)

    # --- Внутренняя механика ---

    def _window(self, chat_id: int) -> _SlidingWindow:
        window = self._chat_windows.get(chat_id)
        if window is None:
            window = _SlidingWindow(self._per_chat_limit, self._per_chat_window)
            self._chat_windows[chat_id] = window
        return window

    async def _call(self, chat_id: int, action: Callable[[], Awaitable[Any]]) -> Any:
        """Выполняет запрос: лимиты, повторы после RetryAfter, недоступный чат."""
        for attempt in range(1, self._retry_attempts + 1):
            await self._window(chat_id).acquire()
            await self._global.acquire()
            try:
                return await action()
            except TelegramRetryAfter as exc:
                logger.warning(
                    "Telegram просит подождать %s с (чат %s, попытка %d)",
                    exc.retry_after,
                    chat_id,
                    attempt,
                )
                if attempt == self._retry_attempts:
                    raise ChatUnavailable(f"лимит Telegram: {exc}") from exc
                await asyncio.sleep(exc.retry_after + 1)
            except TelegramForbiddenError as exc:
                logger.warning("Нет доступа к чату %s: %s", chat_id, exc)
                raise ChatUnavailable(str(exc)) from exc
            except TelegramBadRequest as exc:
                if self._is_chat_unavailable(exc):
                    logger.warning("Чат %s недоступен: %s", chat_id, exc)
                    raise ChatUnavailable(str(exc)) from exc
                raise
        raise AssertionError("цикл повторов завершился без результата")  # pragma: no cover

    @staticmethod
    def _is_chat_unavailable(exc: TelegramBadRequest) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in UNAVAILABLE_MARKERS)
