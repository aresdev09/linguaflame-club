"""Тесты Sender: лимитеры, повторы после RetryAfter и недоступный чат."""

import time
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from bot.sender import ChatUnavailable, Sender


def _retry_after(retry_after: int) -> TelegramRetryAfter:
    return TelegramRetryAfter(
        method=AsyncMock(), message="Too Many Requests", retry_after=retry_after
    )


def _make_sender(**kwargs: object) -> tuple[Sender, AsyncMock]:
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=AsyncMock(message_id=1))
    bot.edit_message_text = AsyncMock(return_value=AsyncMock(message_id=1))
    defaults: dict[str, object] = {
        "per_chat_limit": 100,
        "per_chat_window": 60.0,
        "global_limit": 100,
        "global_window": 1.0,
    }
    defaults.update(kwargs)
    return Sender(bot, **defaults), bot


async def test_per_chat_limiter_waits_when_window_is_full() -> None:
    """Быстрая серия сообщений в один чат ждёт освобождения окна."""
    sender, bot = _make_sender(per_chat_limit=2, per_chat_window=0.5)

    started = time.monotonic()
    for _ in range(4):
        await sender.send(-100, "hi")
    elapsed = time.monotonic() - started

    assert bot.send_message.await_count == 4
    # Первые два уходят сразу, следующие ждут полное окно.
    assert elapsed >= 0.4


async def test_global_limiter_works_across_chats() -> None:
    """Глобальный лимит считает сообщения по всем чатам вместе."""
    sender, bot = _make_sender(global_limit=2, global_window=0.5)

    started = time.monotonic()
    for chat_id in (-100, -101, -102, -103):
        await sender.send(chat_id, "hi")
    elapsed = time.monotonic() - started

    assert bot.send_message.await_count == 4
    assert elapsed >= 0.4


async def test_retry_after_waits_and_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    """После TelegramRetryAfter отправка повторяется с паузой."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("bot.sender.asyncio.sleep", fake_sleep)

    sender, bot = _make_sender(retry_attempts=2)
    bot.send_message = AsyncMock(side_effect=[_retry_after(1), AsyncMock(message_id=7)])

    result = await sender.send(-100, "hi")

    assert result.message_id == 7
    assert sleeps and sleeps[-1] == 2  # retry_after + 1


async def test_retry_after_exhausted_raises_chat_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("bot.sender.asyncio.sleep", fake_sleep)

    sender, bot = _make_sender(retry_attempts=2)
    bot.send_message = AsyncMock(side_effect=_retry_after(1))

    with pytest.raises(ChatUnavailable):
        await sender.send(-100, "hi")


async def test_forbidden_error_raises_chat_unavailable() -> None:
    """Бота удалили из группы — чат помечается недоступным."""
    sender, bot = _make_sender()
    bot.send_message = AsyncMock(
        side_effect=TelegramForbiddenError(
            method=AsyncMock(), message="Forbidden: bot was kicked from the group chat"
        )
    )

    with pytest.raises(ChatUnavailable):
        await sender.send(-100, "hi")


async def test_chat_not_found_raises_chat_unavailable() -> None:
    sender, bot = _make_sender()
    bot.send_message = AsyncMock(
        side_effect=TelegramBadRequest(method=AsyncMock(), message="Bad Request: chat not found")
    )

    with pytest.raises(ChatUnavailable):
        await sender.send(-100, "hi")


async def test_edit_not_modified_is_not_an_error() -> None:
    """Повторная правка без изменений не считается ошибкой."""
    sender, bot = _make_sender()
    bot.edit_message_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=AsyncMock(), message="Bad Request: message is not modified"
        )
    )

    assert await sender.edit(-100, 1, "same text") is None
