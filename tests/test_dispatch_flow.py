"""Интеграционные тесты: апдейты проходят через диспетчер целиком.

Проверяются сценарии, которые пользователь делает руками в Telegram:
бот добавлен → приветствие; участник пишет /setup → отказ; админ
пишет /setup → меню; бота кикнули → чат неактивен; /quiz → раунд
с кнопками; ответ по кнопке → начисление очков и callback.answer.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus
from aiogram.types import (
    CallbackQuery,
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
from bot.repositories import ChatRepo, GameRepo, MemberRepo, ScoreRepo
from bot.routers.quiz import parse_rounds
from bot.sender import Sender
from bot.services.content import ContentService
from bot.services.daily import DailyService
from bot.services.daily_callbacks import parse_daily_callback
from bot.services.fake_ai_client import FakeAIClient
from bot.services.games.callbacks import parse_answer_callback
from bot.services.games.manager import GameManager
from bot.services.scoring import ScoringService
from bot.services.streak import StreakService

BOT_USER = User(id=42, is_bot=True, first_name="Club bot")
GROUP = Chat(id=-700, type="group", title="Study group")

_update_id = 0


def _next_update_id() -> int:
    global _update_id
    _update_id += 1
    return _update_id


def _settings() -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None)


def _games(
    session_factory: async_sessionmaker[AsyncSession],
    sender: Sender,
    settings: Settings,
) -> GameManager:
    """Движок игр как в продакшене, но с фальшивым ИИ."""
    content = ContentService(session_factory, FakeAIClient(), settings)
    scoring = ScoringService(session_factory, settings)
    streak = StreakService(session_factory)
    return GameManager(
        session_factory=session_factory,
        settings=settings,
        sender=sender,
        content=content,
        scoring=scoring,
        streak=streak,
    )


def _daily(
    session_factory: async_sessionmaker[AsyncSession],
    sender: Sender,
    settings: Settings,
    client: FakeAIClient | None = None,
) -> DailyService:
    """Сервис дня как в продакшене, но с фальшивым ИИ."""
    content = ContentService(session_factory, client or FakeAIClient(), settings)
    return DailyService(
        session_factory=session_factory,
        settings=settings,
        content=content,
        scoring=ScoringService(session_factory, settings),
        streak=StreakService(session_factory),
    )


def _harness(
    session_factory: async_sessionmaker[AsyncSession],
    member_status: str = ChatMemberStatus.MEMBER,
) -> tuple[AsyncMock, Sender, AdminCache, object, GameManager]:
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    bot.edit_message_text = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=member_status))
    sender = Sender(bot, per_chat_limit=100, global_limit=100)
    admins = AdminCache(bot)
    settings = _settings()
    games = _games(session_factory, sender, settings)
    dispatcher = build_dispatcher(session_factory, sender, settings)
    return bot, sender, admins, dispatcher, games


async def _feed(
    dispatcher: object,
    bot: AsyncMock,
    sender: Sender,
    admins: AdminCache,
    session_factory: async_sessionmaker[AsyncSession],
    update: Update,
    games: GameManager,
    daily: DailyService | None = None,
) -> None:
    await dispatcher.feed_update(
        bot,
        update,
        session_factory=session_factory,
        sender=sender,
        admins=admins,
        settings=_settings(),
        games=games,
        daily=daily or _daily(session_factory, sender, _settings()),
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


def _callback_update(user_id: int, data: str, message_id: int = 500) -> Update:
    return Update(
        update_id=_next_update_id(),
        callback_query=CallbackQuery(
            id=str(_next_update_id()),
            from_user=User(id=user_id, is_bot=False, first_name=f"User{user_id}"),
            message=Message(
                message_id=message_id,
                date=datetime.now(UTC),
                chat=GROUP,
            ),
            chat_instance=str(GROUP.id),
            data=data,
        ),
    )


def _reply_update(user_id: int, text: str, reply_to_message_id: int) -> Update:
    return Update(
        update_id=_next_update_id(),
        message=Message(
            message_id=_next_update_id(),
            date=datetime.now(UTC),
            chat=GROUP,
            from_user=User(id=user_id, is_bot=False, first_name=f"User{user_id}"),
            text=text,
            reply_to_message=Message(
                message_id=reply_to_message_id,
                date=datetime.now(UTC),
                chat=GROUP,
                from_user=BOT_USER,
            ),
        ),
    )


async def test_dispatcher_greets_when_bot_is_added(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, sender, admins, dispatcher, games = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _membership_update(
            ChatMemberLeft(user=BOT_USER), ChatMemberMember(user=BOT_USER)
        ),
        games,
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
    bot, sender, admins, dispatcher, games = _harness(session_factory)

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
        games,
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
        games,
    )

    async with session_factory() as session:
        chat = await ChatRepo(session).get(-700)
        assert chat is not None
        assert chat.active is False


async def test_dispatcher_answers_top_command(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Команда /top проходит через диспетчер и отвечает рейтингом."""
    bot, sender, admins, dispatcher, games = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _command_update(user_id=11, text="/top"),
        games,
    )

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert "No scores yet" in kwargs["text"]
    assert kwargs["reply_markup"] is not None


async def test_dispatcher_refuses_setup_from_regular_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, sender, admins, dispatcher, games = _harness(session_factory)

    await _feed(
        dispatcher, bot, sender, admins, session_factory, _command_update(user_id=8), games
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
    bot, sender, admins, dispatcher, games = _harness(
        session_factory, member_status=ChatMemberStatus.ADMINISTRATOR
    )

    await _feed(
        dispatcher, bot, sender, admins, session_factory, _command_update(user_id=9), games
    )

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert "B1" in kwargs["text"]
    assert kwargs["reply_markup"] is not None


async def test_dispatcher_runs_quiz_and_answers_buttons(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/quiz открывает раунд, кнопка начисляет очки, повтор — отказ."""
    bot, sender, admins, dispatcher, games = _harness(session_factory)
    try:
        await _feed(
            dispatcher,
            bot,
            sender,
            admins,
            session_factory,
            _command_update(user_id=11, text="/quiz 2"),
            games,
        )

        # Сообщение первого раунда с кнопками A–D.
        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        assert "Quiz — round 1/2" in kwargs["text"]
        buttons = [
            button
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        assert [button.text for button in buttons] == ["A", "B", "C", "D"]
        session_id, round_no, _choice = parse_answer_callback(buttons[0].callback_data)
        assert round_no == 1

        # Участник нажимает кнопку: ответ принимается и «часики» убираются.
        # (`callback.answer()` у замоканного бота превращается в bot(method).)
        await _feed(
            dispatcher,
            bot,
            sender,
            admins,
            session_factory,
            _callback_update(user_id=11, data=buttons[0].callback_data),
            games,
        )
        bot.assert_awaited_once()
        method = bot.await_args.args[0]
        assert "Correct" in method.text
        assert "+" in method.text

        # Повторное нажатие — уже отвечал в этом раунде.
        await _feed(
            dispatcher,
            bot,
            sender,
            admins,
            session_factory,
            _callback_update(user_id=11, data=buttons[0].callback_data),
            games,
        )
        assert bot.await_count == 2
        assert "already answered" in bot.await_args.args[0].text
    finally:
        # Гасим таймеры сессии, оставшейся «идти» после теста.
        await games.shutdown()

    # Очки за один верный ответ записаны в журнал с привязкой к сессии.
    async with session_factory() as session:
        row = await GameRepo(session).get_active(GROUP.id)
        assert row is not None and row.id == session_id
        answers = await GameRepo(session).round_answers(session_id, 1)
    assert [a.user_id for a in answers] == [11]


async def test_dispatcher_game_menu_starts_selected_game(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/games показывает меню, кнопка запускает выбранную мини-игру."""
    bot, sender, admins, dispatcher, games = _harness(session_factory)
    try:
        await _feed(
            dispatcher,
            bot,
            sender,
            admins,
            session_factory,
            _command_update(user_id=11, text="/games"),
            games,
        )
        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        assert "Games" in kwargs["text"]
        buttons = [
            button
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        assert [button.callback_data for button in buttons] == [
            "games:scramble",
            "games:gap",
            "games:race",
            "games:truefalse",
        ]

        # Выбор игры: «часики» убираются сразу, затем приходит первый раунд.
        bot.send_message.reset_mock()
        await _feed(
            dispatcher,
            bot,
            sender,
            admins,
            session_factory,
            _callback_update(user_id=11, data="games:gap"),
            games,
        )
        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        assert "Fill the gap — 1/1" in kwargs["text"]
        assert "___" in kwargs["text"]
    finally:
        await games.shutdown()


async def test_dispatcher_scramble_takes_only_reply_to_round_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Ответ учитывается только в reply на сообщение игры, регистр не важен."""
    bot, sender, admins, dispatcher, games = _harness(session_factory)
    try:
        await _feed(
            dispatcher,
            bot,
            sender,
            admins,
            session_factory,
            _command_update(user_id=11, text="/scramble"),
            games,
        )
        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        assert "Word scramble" in kwargs["text"]
        game_message_id = 1  # замоканный Telegram возвращает message_id=1

        # Reply на чужое сообщение бот молча игнорирует.
        bot.send_message.reset_mock()
        await _feed(
            dispatcher,
            bot,
            sender,
            admins,
            session_factory,
            _reply_update(user_id=11, text="apple", reply_to_message_id=999),
            games,
        )
        bot.send_message.assert_not_awaited()

        # Reply на сообщение игры с верным словом: ответ и очки.
        await _feed(
            dispatcher,
            bot,
            sender,
            admins,
            session_factory,
            _reply_update(user_id=11, text="APPLE", reply_to_message_id=game_message_id),
            games,
        )
        bot.send_message.assert_awaited_once()
        assert "Correct" in bot.send_message.await_args.kwargs["text"]
    finally:
        await games.shutdown()


async def test_dispatcher_word_post_and_got_it_award(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/word публикует пост дня, кнопка «Got it» даёт очки один раз в день."""
    bot, sender, admins, dispatcher, games = _harness(session_factory)
    client = FakeAIClient()
    daily = _daily(session_factory, sender, _settings(), client)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _command_update(user_id=11, text="/word"),
        games,
        daily,
    )

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert "Words of the day (B1)" in kwargs["text"]
    buttons = [button for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert [button.text for button in buttons] == ["Got it"]
    action, _post_id = parse_daily_callback(buttons[0].callback_data)
    assert action == "got"

    # Нажатие кнопки: «часики» убраны, очки в журнале.
    bot.reset_mock()
    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _callback_update(user_id=11, data=buttons[0].callback_data),
        games,
        daily,
    )
    assert bot.await_args.args[0].text == "✅ +3"
    async with session_factory() as session:
        assert await ScoreRepo(session).total(GROUP.id, 11) == 3

    # Повторное нажатие: очков не прибавляет, ИИ не вызывался.
    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _callback_update(user_id=11, data=buttons[0].callback_data),
        games,
        daily,
    )
    assert "already got today's points" in bot.await_args.args[0].text
    async with session_factory() as session:
        assert await ScoreRepo(session).total(GROUP.id, 11) == 3
    assert len(client.calls_of("words")) == 1

    # Очки кнопки видны в рейтинге группы.
    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _command_update(user_id=12, text="/top"),
        games,
        daily,
    )
    assert "🥇 User11 — 3 pts" in bot.send_message.await_args.kwargs["text"]


async def test_dispatcher_refuses_reset_scores_for_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Обычный участник не может сбросить рейтинг: короткий отказ."""
    bot, sender, admins, dispatcher, games = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _command_update(user_id=8, text="/reset_scores"),
        games,
    )

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["text"] == en.TEXTS["common.not_admin"]


async def test_dispatcher_reset_scores_needs_confirmation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Админ получает кнопку подтверждения, а сброс делается только по ней."""
    bot, sender, admins, dispatcher, games = _harness(
        session_factory, member_status=ChatMemberStatus.ADMINISTRATOR
    )

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _command_update(user_id=9, text="/reset_scores"),
        games,
    )
    buttons = [
        button
        for row in bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert [button.callback_data for button in buttons] == ["reset:confirm", "reset:cancel"]

    # В группе есть очки — сбросу есть что отменять.
    async with session_factory() as session:
        async with session.begin():
            await ScoreRepo(session).add_event(GROUP.id, 9, points=10, source="quiz")

    # Подтверждение: сообщение правится, рейтинг пуст, есть кнопка возврата.
    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _callback_update(user_id=9, data="reset:confirm"),
        games,
    )
    edited = bot.edit_message_text.await_args.kwargs["text"]
    assert "Leaderboard reset" in edited


async def test_dispatcher_privacy_menu_for_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """/privacy показывает, что хранит бот, и кнопку скрыться из рейтинга."""
    bot, sender, admins, dispatcher, games = _harness(session_factory)

    await _feed(
        dispatcher,
        bot,
        sender,
        admins,
        session_factory,
        _command_update(user_id=11, text="/privacy"),
        games,
    )

    kwargs = bot.send_message.await_args.kwargs
    assert "Your data" in kwargs["text"]
    buttons = [
        button for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert [button.callback_data for button in buttons] == ["privacy:hide"]


def test_parse_rounds_defaults_and_maximum() -> None:
    assert parse_rounds("/quiz") == 5
    assert parse_rounds("/quiz@ClubBot 7") == 7
    assert parse_rounds("/quiz 99") == 10  # максимум
    assert parse_rounds("/quiz abc") == 5
    assert parse_rounds("/quiz 0") == 5
    assert parse_rounds(None) == 5
