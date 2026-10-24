"""Интеграционные тесты: апдейты проходят через диспетчер целиком.

Проверяются сценарии, которые пользователь делает руками в Telegram:
бот добавлен → приветствие; участник пишет /setup → отказ; админ
пишет /setup → меню; бота кикнули → чат неактивен.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus
from aiogram.types import (
    Chat,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    Update,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import AdminCache
from bot.config import Settings
from bot.locales import en
from bot.main import build_dispatcher
from bot.repositories import ChatRepo, MemberRepo
from bot.sender import Sender

BOT_USER = User(id=42, is_bot=True, first_name="Club bot")
GROUP = Chat(id=-700, type="group", title="Study group")

_update_id = 0


def _next_update_id() -> int:
    global _update_id
    _update_id += 1
    return _update_id


def _settings() -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None)


def _harness(
    session_factory: async_sessionmaker[AsyncSession],
    member_status: str = ChatMemberStatus.MEMBER,
) -> tuple[AsyncMock, Sender, AdminCache, object]:
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=member_status))
    sender = Sender(bot, per_chat_limit=100, global_limit=100)
    admins = AdminCache(bot)
    dispatcher = build_dispatcher(session_factory, sender, _settings())
    return bot, sender, admins, dispatcher


async def _feed(
    dispatcher: object,
    bot: AsyncMock,
    sender: Sender,
    admins: AdminCache,
    session_factory: async_sessionmaker[AsyncSession],
    update: Update,
) -> None:
    await dispatcher.feed_update(
        bot,
        update,
        session_factory=session_factory,
        sender=sender,
        admins=admins,
        settings=_settings(),
    )


def _membership_update(old_status: object, new_status: object) -> Update:
    return Update(
        update_id=_next_update_id(),
        my_chat_member=ChatMemberUpdated(
            chat=GROUP,
            from_user=User(id=7, is_bot=False, first_name="Admin"),
            date=datetime.now(UTC),
            old_chat_member=old_status,
            new_chat_member=new_status,
        ),
    )


def _command_update(user_id: int, text: str = "/setup") -> Update:
    return Update(
        update_id=_next_update_id(),
        message=Message(
            message_id=_next_update_id(),
            date=datetime.now(UTC),
            chat=GROUP,
            from_user=User(id=user_id, is_bot=False, first_name=f"User{user_id}"),
            text=text,
        ),
    )


async def test_dispatcher_greets_when_bot_is_added(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, sender, admins, dispatcher = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _membership_update(
            ChatMemberLeft(user=BOT_USER), ChatMemberMember(user=BOT_USER)
        ),
    )

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == -700
    assert "/setup" in bot.send_message.await_args.kwargs["text"]
    async with session_factory() as session:
        chat = await ChatRepo(session).get(-700)
        assert chat is not None
        assert chat.active is True


async def test_dispatcher_deactivates_when_bot_kicked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, sender, admins, dispatcher = _harness(session_factory)

    # Сначала бота добавили — чат появился.
    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _membership_update(
            ChatMemberLeft(user=BOT_USER), ChatMemberMember(user=BOT_USER)
        ),
    )
    # Потом забанили — чат становится неактивным, данные остаются.
    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _membership_update(
            ChatMemberMember(user=BOT_USER),
            ChatMemberBanned(user=BOT_USER, until_date=datetime.now(UTC)),
        ),
    )

    async with session_factory() as session:
        chat = await ChatRepo(session).get(-700)
        assert chat is not None
        assert chat.active is False


async def test_dispatcher_answers_top_command(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Команда /top проходит через диспетчер и отвечает рейтингом."""
    bot, sender, admins, dispatcher = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _command_update(user_id=11, text="/top"),
    )

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert "No scores yet" in kwargs["text"]
    assert kwargs["reply_markup"] is not None


async def test_dispatcher_refuses_setup_from_regular_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, sender, admins, dispatcher = _harness(session_factory)

    await _feed(
        dispatcher, bot, sender, admins, session_factory, _command_update(user_id=8)
    )

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["text"] == en.TEXTS["common.not_admin"]
    # Действие участника в группе попадает в members.
    async with session_factory() as session:
        member = await MemberRepo(session).get(-700, 8)
        assert member is not None


async def test_dispatcher_opens_menu_for_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, sender, admins, dispatcher = _harness(
        session_factory, member_status=ChatMemberStatus.ADMINISTRATOR
    )

    await _feed(
        dispatcher, bot, sender, admins, session_factory, _command_update(user_id=9)
    )

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert "B1" in kwargs["text"]
    assert kwargs["reply_markup"] is not None
