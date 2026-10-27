"""Тесты жизненного цикла бота в группе: добавление, удаление и возвращение."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.repositories import ChatRepo
from bot.routers.group_admin import (
    bot_membership_changed,
    on_group_upgrade_seen_in_supergroup,
    on_group_upgraded,
)
from bot.services.group_lifecycle import (
    migrate_chat,
    register_bot_added,
    register_bot_removed,
)

BOT_USER = User(id=42, is_bot=True, first_name="Club bot")


def _administrator() -> ChatMemberAdministrator:
    return ChatMemberAdministrator(
        user=BOT_USER,
        can_be_edited=False,
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=True,
        can_restrict_members=True,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_send_welcome_messages=False,
    )


def _membership_update(chat: Chat, old_status: str, new_status: str) -> ChatMemberUpdated:
    members = {
        ChatMemberStatus.LEFT: ChatMemberLeft(user=BOT_USER),
        ChatMemberStatus.MEMBER: ChatMemberMember(user=BOT_USER),
        ChatMemberStatus.ADMINISTRATOR: _administrator(),
    }
    return ChatMemberUpdated(
        chat=chat,
        from_user=User(id=7, is_bot=False, first_name="Admin"),
        date=datetime.now(UTC),
        old_chat_member=members[old_status],
        new_chat_member=members[new_status],
    )


async def test_added_creates_chat_with_defaults(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await register_bot_added(
        session_factory, chat_id=-100, title="Study group", chat_type="group"
    )
    assert chat.active is True
    assert chat.level == "B1"
    assert chat.ui_language == "en"
    assert chat.daily_words is False
    assert chat.daily_grammar is False

    async with session_factory() as session:
        stored = await ChatRepo(session).get(-100)
        assert stored is not None
        assert stored.title == "Study group"


async def test_removed_deactivates_chat(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await register_bot_added(session_factory, chat_id=-100, title="G", chat_type="group")
    await register_bot_removed(session_factory, chat_id=-100)

    async with session_factory() as session:
        stored = await ChatRepo(session).get(-100)
        assert stored is not None
        assert stored.active is False
        assert stored.deactivated_at is not None


async def test_readd_keeps_settings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Удаление и повторное добавление бота не теряют настройки."""
    await register_bot_added(session_factory, chat_id=-100, title="G", chat_type="group")
    async with session_factory() as session:
        await ChatRepo(session).update_settings(-100, level="C1", daily_words=True)
        await session.commit()

    await register_bot_removed(session_factory, chat_id=-100)
    await register_bot_added(session_factory, chat_id=-100, title="G", chat_type="supergroup")

    async with session_factory() as session:
        stored = await ChatRepo(session).get(-100)
        assert stored is not None
        assert stored.active is True
        assert stored.deactivated_at is None
        assert stored.level == "C1"
        assert stored.daily_words is True


async def test_membership_handler_greets_and_deactivates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """События my_chat_member: приветствие при добавлении, деактивация при удалении."""
    sender = AsyncMock()
    chat = Chat(id=-200, type="group", title="Test group")

    await bot_membership_changed(
        update=_membership_update(chat, ChatMemberStatus.LEFT, ChatMemberStatus.MEMBER),
        session_factory=session_factory,
        sender=sender,
    )
    sender.send.assert_awaited_once()
    greeting = sender.send.await_args.args[1]
    assert "/setup" in greeting

    await bot_membership_changed(
        update=_membership_update(chat, ChatMemberStatus.MEMBER, ChatMemberStatus.LEFT),
        session_factory=session_factory,
        sender=sender,
    )
    async with session_factory() as session:
        stored = await ChatRepo(session).get(-200)
        assert stored is not None
        assert stored.active is False


async def test_membership_handler_ignores_promotion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Повышение бота до администратора — не добавление, приветствия нет."""
    sender = AsyncMock()
    chat = Chat(id=-201, type="group", title="Test group")
    await register_bot_added(session_factory, chat_id=-201, title="Test group", chat_type="group")

    await bot_membership_changed(
        update=_membership_update(chat, ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR),
        session_factory=session_factory,
        sender=sender,
    )
    sender.send.assert_not_awaited()


async def test_migrate_moves_chat_and_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Перенос чата в супергруппу; повторный вызов ничего не ломает."""
    old_id, new_id = -300, -1000000000300
    await register_bot_added(session_factory, chat_id=old_id, title="G", chat_type="group")

    await migrate_chat(session_factory, old_chat_id=old_id, new_chat_id=new_id)
    await migrate_chat(session_factory, old_chat_id=old_id, new_chat_id=new_id)

    async with session_factory() as session:
        assert await ChatRepo(session).get(old_id) is None
        moved = await ChatRepo(session).get(new_id)
        assert moved is not None
        assert moved.title == "G"


async def test_migration_message_handlers_move_chat(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Обработчики обоих служебных сообщений о миграции переносят данные."""
    old_id, new_id = -301, -1000000000301
    await register_bot_added(session_factory, chat_id=old_id, title="G", chat_type="group")

    upgraded = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=old_id, type="group", title="G"),
        migrate_to_chat_id=new_id,
    )
    await on_group_upgraded(message=upgraded, session_factory=session_factory)

    seen_in_supergroup = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=Chat(id=new_id, type="supergroup", title="G"),
        migrate_from_chat_id=old_id,
    )
    await on_group_upgrade_seen_in_supergroup(
        message=seen_in_supergroup, session_factory=session_factory
    )

    async with session_factory() as session:
        assert await ChatRepo(session).get(old_id) is None
        moved = await ChatRepo(session).get(new_id)
        assert moved is not None
