"""Личный чат и онбординг через диспетчер целиком.

Проверяются сценарии, которые человек делает руками: `/start` в личке
показывает кнопку «Add to group», `/help` даёт справку по ролям, а
команды игр и админов в личке не падают и вежливо объясняют, что
работают в группе.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import AdminCache
from bot.config import Settings
from bot.locales import en, ru
from bot.main import build_dispatcher
from bot.routers.private import _normalize_username, bot_username
from bot.sender import Sender

PRIVATE = Chat(id=555, type="private", first_name="Anna")
GROUP = Chat(id=-700, type="group", title="Study group")

_update_id = 0


def _next_update_id() -> int:
    global _update_id
    _update_id += 1
    return _update_id


def _settings(bot_username: str = "ClubBot") -> Settings:
    return Settings(bot_token="123:TEST", bot_username=bot_username, _env_file=None)


def _harness(
    session_factory: async_sessionmaker[AsyncSession],
    bot_username: str = "ClubBot",
) -> tuple[AsyncMock, Sender, AdminCache, object, Settings]:
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    sender = Sender(bot, per_chat_limit=100, global_limit=100)
    admins = AdminCache(bot)
    settings = _settings(bot_username)
    dispatcher = build_dispatcher(session_factory, sender, settings)
    return bot, sender, admins, dispatcher, settings


async def _feed(
    dispatcher: object,
    bot: AsyncMock,
    sender: Sender,
    admins: AdminCache,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    update: Update,
) -> None:
    await dispatcher.feed_update(
        bot,
        update,
        session_factory=session_factory,
        sender=sender,
        admins=admins,
        settings=settings,
    )


def _command_update(
    chat: Chat, text: str, *, user_id: int = 7, language_code: str | None = None
) -> Update:
    return Update(
        update_id=_next_update_id(),
        message=Message(
            message_id=_next_update_id(),
            date=datetime.now(UTC),
            chat=chat,
            from_user=User(
                id=user_id,
                is_bot=False,
                first_name=f"User{user_id}",
                language_code=language_code,
            ),
            text=text,
        ),
    )


def _button_urls(markup: object) -> list[str]:
    return [button.url for row in markup.inline_keyboard for button in row]


async def test_private_start_shows_add_to_group_button(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/start в личке: описание возможностей и кнопка добавления в группу."""
    bot, sender, admins, dispatcher, settings = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        settings,
        _command_update(PRIVATE, "/start"),
    )

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert "LinguaFlame Club" in kwargs["text"]
    assert "/setup" in kwargs["text"]
    assert _button_urls(kwargs["reply_markup"]) == [
        "https://t.me/ClubBot?startgroup=true"
    ]


async def test_private_game_command_explains_it_works_in_a_group(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Команда игры в личке не падает: понятный отказ и кнопка."""
    bot, sender, admins, dispatcher, settings = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        settings,
        _command_update(PRIVATE, "/quiz 3"),
    )

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["text"] == en.TEXTS["private.group_only"]
    assert _button_urls(kwargs["reply_markup"]) == [
        "https://t.me/ClubBot?startgroup=true"
    ]


async def test_private_admin_command_does_not_crash(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Админская команда в личке тоже получает отказ, а не ошибку."""
    bot, sender, admins, dispatcher, settings = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        settings,
        _command_update(PRIVATE, "/reset_scores"),
    )

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["text"] == en.TEXTS["private.group_only"]


async def test_private_refusal_uses_user_language(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Русская локаль клиента — отказ на русском."""
    bot, sender, admins, dispatcher, settings = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        settings,
        _command_update(PRIVATE, "/scramble", language_code="ru-RU"),
    )

    assert bot.send_message.await_args.kwargs["text"] == ru.TEXTS["private.group_only"]


async def test_private_help_lists_commands_by_role(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/help в личке: команды участника и админа."""
    bot, sender, admins, dispatcher, settings = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        settings,
        _command_update(PRIVATE, "/help"),
    )

    text = bot.send_message.await_args.kwargs["text"]
    assert en.TEXTS["help.title"] in text
    assert "/quiz" in text and "/privacy" in text
    assert "/setup" in text and "/reset_scores" in text


async def test_group_help_lists_commands(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/help в группе отвечает тем же списком команд."""
    bot, sender, admins, dispatcher, settings = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        settings,
        _command_update(GROUP, "/help"),
    )

    text = bot.send_message.await_args.kwargs["text"]
    assert en.TEXTS["help.title"] in text
    assert "/setup" in text


async def test_group_start_shows_short_help(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/start в группе — короткая справка, а не онбординг из лички."""
    bot, sender, admins, dispatcher, settings = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        settings,
        _command_update(GROUP, "/start"),
    )

    text = bot.send_message.await_args.kwargs["text"]
    assert en.TEXTS["help.title"] in text
    assert "Add to group" not in text


async def test_bot_username_falls_back_to_get_me() -> None:
    """Имени в настройках нет — берём его у Telegram."""
    bot = AsyncMock()
    bot.me = AsyncMock(return_value=SimpleNamespace(username="RealBot"))

    assert await bot_username(bot, _settings(bot_username="")) == "RealBot"


def test_normalize_username_strips_at_sign() -> None:
    assert _normalize_username("@ClubBot") == "ClubBot"
    assert _normalize_username("ClubBot") == "ClubBot"
    assert _normalize_username("") == ""
    assert _normalize_username(None) == ""
