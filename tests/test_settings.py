"""Тесты настроек группы: сводка, применение значений и права в /setup."""

from datetime import UTC, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus
from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import AdminCache
from bot.locales import en
from bot.repositories import ChatRepo
from bot.routers.group_admin import change_setting, open_settings
from bot.services.group_lifecycle import register_bot_added
from bot.services.settings import (
    apply_callback_setting,
    apply_manual_setting,
    parse_quiet,
    parse_time,
    settings_summary,
)

CHAT_ID = -400
GROUP = Chat(id=CHAT_ID, type="group", title="Study group")
ADMIN_USER = User(id=5, is_bot=False, first_name="Admin")
MEMBER_USER = User(id=6, is_bot=False, first_name="Member")


async def _chat_row(session_factory: async_sessionmaker[AsyncSession]):
    await register_bot_added(
        session_factory, chat_id=CHAT_ID, title="Study group", chat_type="group"
    )
    async with session_factory() as session:
        return await ChatRepo(session).get(CHAT_ID)


def _admins(member_status: str) -> AdminCache:
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=member_status))
    return AdminCache(bot)


def _fake_state(data: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        clear=AsyncMock(),
        set_state=AsyncMock(),
        update_data=AsyncMock(),
        get_data=AsyncMock(return_value=data or {}),
    )


def _message(user: User, text: str = "/setup") -> Message:
    return Message(
        message_id=100,
        date=datetime.now(UTC),
        chat=GROUP,
        from_user=user,
        text=text,
    )


async def test_summary_shows_current_settings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)

    text_en = settings_summary(chat, "en")
    assert "B1" in text_en
    assert "10" in text_en
    assert "UTC" in text_en
    assert "09:00" in text_en
    assert "22:00–07:00" in text_en

    text_ru = settings_summary(chat, "ru")
    assert "Уровень" in text_ru


async def test_apply_callback_setting_changes_values(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat_row(session_factory)

    await apply_callback_setting(session_factory, CHAT_ID, "set:level:B2")
    await apply_callback_setting(session_factory, CHAT_ID, "set:count:20")
    await apply_callback_setting(session_factory, CHAT_ID, "set:ui:ru")
    await apply_callback_setting(session_factory, CHAT_ID, "set:translation:uz")
    await apply_callback_setting(session_factory, CHAT_ID, "set:time:19:30")
    await apply_callback_setting(session_factory, CHAT_ID, "set:quiet:23:00-08:00")

    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
        assert chat is not None
        assert chat.level == "B2"
        assert chat.words_count == 20
        assert chat.ui_language == "ru"
        assert chat.translation_language == "uz"
        assert chat.daily_time == time(19, 30)
        assert (chat.quiet_from, chat.quiet_to) == (time(23, 0), time(8, 0))


async def test_apply_callback_setting_toggles_daily_posts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat_row(session_factory)

    await apply_callback_setting(session_factory, CHAT_ID, "set:words")
    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
        assert chat is not None and chat.daily_words is True

    await apply_callback_setting(session_factory, CHAT_ID, "set:words")
    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
        assert chat is not None and chat.daily_words is False


async def test_invalid_callback_values_are_ignored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat_row(session_factory)

    await apply_callback_setting(session_factory, CHAT_ID, "set:level:Z9")
    await apply_callback_setting(session_factory, CHAT_ID, "set:timezone:Mars/Olympus")
    await apply_callback_setting(session_factory, CHAT_ID, "set:time:99:99")
    await apply_callback_setting(session_factory, CHAT_ID, "set:unknown:x")

    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
        assert chat is not None
        assert chat.level == "B1"
        assert chat.timezone == "UTC"
        assert chat.daily_time == time(9, 0)


async def test_apply_manual_setting_validates_input(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat_row(session_factory)

    bad_zone = await apply_manual_setting(session_factory, CHAT_ID, "timezone", "Mars/Olympus")
    assert bad_zone == "setup.bad_timezone"
    bad_time = await apply_manual_setting(session_factory, CHAT_ID, "time", "25:99")
    assert bad_time == "setup.bad_time"

    good_zone = await apply_manual_setting(session_factory, CHAT_ID, "timezone", "Asia/Samarkand")
    assert good_zone is None
    assert await apply_manual_setting(session_factory, CHAT_ID, "time", "7:05") is None

    async with session_factory() as session:
        chat = await ChatRepo(session).get(CHAT_ID)
        assert chat is not None
        assert chat.timezone == "Asia/Samarkand"
        assert chat.daily_time == time(7, 5)


def test_parse_helpers() -> None:
    assert parse_time("09:00") == time(9, 0)
    assert parse_time("24:00") is None
    assert parse_time("nine") is None
    assert parse_quiet("off") == (time(0, 0), time(0, 0))
    assert parse_quiet("22:00-07:00") == (time(22, 0), time(7, 0))
    assert parse_quiet("22:00") is None


async def test_setup_refuses_regular_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)
    sender = AsyncMock()

    await open_settings(
        message=_message(MEMBER_USER),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        admins=_admins(ChatMemberStatus.MEMBER),
        sender=sender,
        state=_fake_state(),
    )

    sender.reply.assert_awaited_once()
    assert sender.reply.await_args.args[1] == en.TEXTS["common.not_admin"]
    assert "reply_markup" not in sender.reply.await_args.kwargs


async def test_setup_opens_menu_for_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)
    sender = AsyncMock()

    await open_settings(
        message=_message(ADMIN_USER),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        admins=_admins(ChatMemberStatus.ADMINISTRATOR),
        sender=sender,
        state=_fake_state(),
    )

    sender.reply.assert_awaited_once()
    markup = sender.reply.await_args.kwargs["reply_markup"]
    assert markup is not None
    texts = [button.text for row in markup.inline_keyboard for button in row]
    assert any("B1" in text for text in texts)


async def test_callback_saves_setting_and_edits_menu(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)
    sender = AsyncMock()
    menu_message = Message(
        message_id=200,
        date=datetime.now(UTC),
        chat=GROUP,
        from_user=User(id=42, is_bot=True, first_name="Bot"),
    )
    callback = CallbackQuery(
        id="1",
        from_user=ADMIN_USER,
        chat_instance="ci",
        data="set:level:C1",
        message=menu_message,
    )

    await change_setting(
        callback=callback,
        session_factory=session_factory,
        chat=chat,
        lang="en",
        admins=_admins(ChatMemberStatus.CREATOR),
        sender=sender,
        state=_fake_state(),
    )

    async with session_factory() as session:
        stored = await ChatRepo(session).get(CHAT_ID)
        assert stored is not None and stored.level == "C1"
    sender.edit.assert_awaited_once()
    sender.answer_callback.assert_awaited_once()


async def test_callback_from_member_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)
    sender = AsyncMock()
    callback = CallbackQuery(
        id="2",
        from_user=MEMBER_USER,
        chat_instance="ci",
        data="set:level:C1",
        message=Message(
            message_id=201,
            date=datetime.now(UTC),
            chat=GROUP,
            from_user=User(id=42, is_bot=True, first_name="Bot"),
        ),
    )

    await change_setting(
        callback=callback,
        session_factory=session_factory,
        chat=chat,
        lang="en",
        admins=_admins(ChatMemberStatus.MEMBER),
        sender=sender,
        state=_fake_state(),
    )

    sender.edit.assert_not_awaited()
    async with session_factory() as session:
        stored = await ChatRepo(session).get(CHAT_ID)
        assert stored is not None and stored.level == "B1"
