"""Тесты ErrorMiddleware: ошибки не роняют бота, недоступный чат гасится."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.locales import en, ru
from bot.middlewares.errors import ErrorMiddleware
from bot.repositories import ChatRepo
from bot.sender import ChatUnavailable
from bot.services.group_lifecycle import register_bot_added

CHAT_ID = -600
CHAT = Chat(id=CHAT_ID, type="group", title="G")
UPDATE = Update(
    update_id=1,
    message=Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=CHAT,
        from_user=User(id=5, is_bot=False, first_name="Anna"),
        text="/setup",
    ),
)


async def _failing_handler(event: Update, data: dict) -> None:
    raise RuntimeError("boom")


async def _create_chat(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await register_bot_added(session_factory, chat_id=CHAT_ID, title="G", chat_type="group")


async def test_handler_error_is_reported_to_group(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _create_chat(session_factory)
    sender = AsyncMock()
    middleware = ErrorMiddleware(session_factory, sender)

    result = await middleware(_failing_handler, UPDATE, {})

    assert result is None
    sender.send.assert_awaited_once()
    assert sender.send.await_args.args[0] == CHAT_ID
    assert sender.send.await_args.args[1] == en.TEXTS["common.error"]


async def test_error_message_uses_chat_language(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _create_chat(session_factory)
    async with session_factory() as session:
        await ChatRepo(session).update_settings(CHAT_ID, ui_language="ru")
        await session.commit()
    sender = AsyncMock()
    middleware = ErrorMiddleware(session_factory, sender)

    await middleware(_failing_handler, UPDATE, {})

    assert sender.send.await_args.args[1] == ru.TEXTS["common.error"]


async def test_chat_unavailable_deactivates_chat(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Бота удалили из группы: сессия не падает, чат становится неактивным."""
    await _create_chat(session_factory)
    sender = AsyncMock()

    async def failing(event: Update, data: dict) -> None:
        raise ChatUnavailable("bot was kicked from the group chat")

    middleware = ErrorMiddleware(session_factory, sender)
    result = await middleware(failing, UPDATE, {})

    assert result is None
    sender.send.assert_not_awaited()
    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
        assert chat is not None
        assert chat.active is False


async def test_notify_failure_also_deactivates_chat(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Даже сообщить об ошибке не удалось — чат помечается неактивным."""
    await _create_chat(session_factory)
    sender = AsyncMock()
    sender.send = AsyncMock(side_effect=ChatUnavailable("no rights"))
    middleware = ErrorMiddleware(session_factory, sender)

    await middleware(_failing_handler, UPDATE, {})

    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
        assert chat is not None
        assert chat.active is False
