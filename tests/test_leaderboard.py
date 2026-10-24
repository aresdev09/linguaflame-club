"""Тесты команд /top и /me: тексты, кнопки периода, свои данные в /me."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import Chat as ChatRow
from bot.repositories import ChatRepo, MemberRepo, ScoreRepo
from bot.routers.leaderboard import show_me, show_top, switch_top

CHAT_ID = -800
GROUP = Chat(id=CHAT_ID, type="group", title="Study group")
ANNA = User(id=55, is_bot=False, first_name="Anna")
BEKZOD = User(id=66, is_bot=False, first_name="Bekzod")
BOT_USER = User(id=42, is_bot=True, first_name="Bot")


def _settings() -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None)


async def _chat_row(session_factory: async_sessionmaker[AsyncSession]) -> ChatRow:
    async with session_factory() as session:
        async with session.begin():
            return await ChatRepo(session).get_or_create(CHAT_ID, title="Study group")


async def _event(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    points: int,
    *,
    display_name: str | None = None,
    at: datetime | None = None,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            if display_name is not None:
                await MemberRepo(session).get_or_create(
                    CHAT_ID, user_id, display_name=display_name
                )
            event = await ScoreRepo(session).add_event(
                CHAT_ID, user_id, points=points, source="quiz"
            )
            if at is not None:
                event.created_at = at


def _message(user: User, text: str = "/top") -> Message:
    return Message(
        message_id=300,
        date=datetime.now(UTC),
        chat=GROUP,
        from_user=user,
        text=text,
    )


def _callback(user: User, data: str) -> CallbackQuery:
    return CallbackQuery(
        id="cb-top",
        from_user=user,
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=301,
            date=datetime.now(UTC),
            chat=GROUP,
            from_user=BOT_USER,
            text="🏆 Leaderboard",
        ),
    )


def _button_texts(markup: object) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_top_shows_week_ranking_with_buttons(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)
    await _event(session_factory, 55, 10, display_name="Anna")
    sender = AsyncMock()

    await show_top(
        message=_message(ANNA),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        sender=sender,
        settings=_settings(),
    )

    text = sender.reply.await_args.args[1]
    assert "🏆 Leaderboard — this week" in text
    assert "🥇 Anna — 10 pts" in text
    markup = sender.reply.await_args.kwargs["reply_markup"]
    assert _button_texts(markup) == ["✅ This week", "All time"]


async def test_top_empty_ranking_shows_hint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)
    sender = AsyncMock()

    await show_top(
        message=_message(ANNA),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        sender=sender,
        settings=_settings(),
    )

    assert "No scores yet. Start with /quiz" in sender.reply.await_args.args[1]


async def test_top_toggle_shows_all_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Кнопка «All time» переключает период и правит то же сообщение."""
    chat = await _chat_row(session_factory)
    # Событие две недели назад: в недельный рейтинг не попадает.
    await _event(
        session_factory,
        66,
        42,
        display_name="Bekzod",
        at=datetime.now(UTC) - timedelta(days=14),
    )
    sender = AsyncMock()

    await show_top(
        message=_message(ANNA),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        sender=sender,
        settings=_settings(),
    )
    assert "No scores yet" in sender.reply.await_args.args[1]

    await switch_top(
        callback=_callback(ANNA, "top:all"),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        sender=sender,
        settings=_settings(),
    )

    chat_id, message_id, text = sender.edit.await_args.args
    assert (chat_id, message_id) == (CHAT_ID, 301)
    assert "🏆 Leaderboard — all time" in text
    assert "🥇 Bekzod — 42 pts" in text
    assert _button_texts(sender.edit.await_args.kwargs["reply_markup"]) == [
        "This week",
        "✅ All time",
    ]
    sender.answer_callback.assert_awaited_once()


async def test_me_shows_own_stats(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)
    await _event(session_factory, 55, 10, display_name="Anna")
    await _event(session_factory, 66, 20, display_name="Bekzod")
    sender = AsyncMock()

    await show_me(
        message=_message(ANNA, text="/me"),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        sender=sender,
        settings=_settings(),
    )

    text = sender.reply.await_args.args[1]
    assert "📊 Your stats" in text
    assert "Points this week: 10" in text
    assert "Points all time: 10" in text
    assert "Place: #2" in text
    assert "Answers: 0 (0% correct)" in text
    assert "Best game: Quiz" in text


async def test_me_of_opted_out_member_has_no_place(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat_row(session_factory)
    await _event(session_factory, 55, 10, display_name="Anna")
    async with session_factory() as session:
        async with session.begin():
            await MemberRepo(session).set_opted_out(CHAT_ID, 55, True)
    sender = AsyncMock()

    await show_me(
        message=_message(ANNA, text="/me"),
        session_factory=session_factory,
        chat=chat,
        lang="en",
        sender=sender,
        settings=_settings(),
    )

    text = sender.reply.await_args.args[1]
    assert "Points this week: 10" in text
    assert "Place: —" in text
