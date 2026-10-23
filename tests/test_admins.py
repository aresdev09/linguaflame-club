"""Тесты проверки прав администратора: статусы, кеш, анонимные админы."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, Message, User

from bot.admins import (
    GROUP_ANONYMOUS_BOT_ID,
    AdminCache,
    check_admin,
    is_anonymous_admin,
)


def _message(
    user: User,
    chat: Chat | None = None,
    sender_chat: Chat | None = None,
) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat or Chat(id=-100, type="group", title="G"),
        from_user=user,
        text="/setup",
        sender_chat=sender_chat,
    )


async def test_creator_and_administrator_are_admins() -> None:
    bot = AsyncMock()
    cache = AdminCache(bot)

    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.CREATOR))
    assert await cache.is_admin(-100, 5) is True

    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)
    )
    assert await cache.is_admin(-100, 6) is True


async def test_regular_member_and_stranger_are_not_admins() -> None:
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER))
    cache = AdminCache(bot)

    assert await cache.is_admin(-100, 5) is False

    # Ответ обычной строкой тоже понимается.
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="left"))
    assert await cache.is_admin(-100, 7) is False


async def test_result_is_cached() -> None:
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)
    )
    cache = AdminCache(bot)

    assert await cache.is_admin(-100, 5) is True
    assert await cache.is_admin(-100, 5) is True
    assert bot.get_chat_member.await_count == 1


async def test_cache_expires_after_ttl() -> None:
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=ChatMemberStatus.MEMBER))
    cache = AdminCache(bot, ttl_seconds=0.05)

    assert await cache.is_admin(-100, 5) is False
    await asyncio.sleep(0.06)
    assert await cache.is_admin(-100, 5) is False
    assert bot.get_chat_member.await_count == 2


async def test_bad_request_means_not_admin() -> None:
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(
        side_effect=TelegramBadRequest(method=AsyncMock(), message="Bad Request: user not found")
    )
    cache = AdminCache(bot)

    assert await cache.is_admin(-100, 999) is False


def test_anonymous_admin_by_sender_id() -> None:
    message = _message(User(id=GROUP_ANONYMOUS_BOT_ID, is_bot=True, first_name="Group"))
    assert is_anonymous_admin(message) is True


def test_anonymous_admin_by_sender_chat() -> None:
    chat = Chat(id=-100, type="group", title="G")
    message = _message(User(id=5, is_bot=False, first_name="A"), chat=chat, sender_chat=chat)
    assert is_anonymous_admin(message) is True


async def test_check_admin_handles_anonymous_without_requests() -> None:
    bot = AsyncMock()
    cache = AdminCache(bot)
    message = _message(User(id=GROUP_ANONYMOUS_BOT_ID, is_bot=True, first_name="Group"))

    assert await check_admin(message, cache) is True
    bot.get_chat_member.assert_not_awaited()


async def test_check_admin_uses_cache_for_regular_user() -> None:
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)
    )
    cache = AdminCache(bot)
    message = _message(User(id=5, is_bot=False, first_name="A"))

    assert await check_admin(message, cache) is True
    assert bot.get_chat_member.await_count == 1
