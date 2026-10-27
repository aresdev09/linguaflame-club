"""Тесты админ-инструментов и приватности (этап 10).

Проверяются права на команды (обычный участник не остановит игру и не
сбросит рейтинг), сброс как отмена с откатом за 24 часа, журнал
действий администраторов и режим приватности: скрывшийся участник
играет, но в рейтинге не виден.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import AdminCache
from bot.config import Settings
from bot.locales import en
from bot.models import Chat as ChatRow
from bot.models import ScoreEvent
from bot.repositories import ChatRepo, MemberRepo, ScoreRepo
from bot.routers import admin as admin_router
from bot.routers.admin import (
    privacy_callback,
    privacy_command,
    reset_callback,
    reset_scores_command,
    stop_command,
)
from bot.services.admin_actions import (
    RESET_EMPTY,
    RESET_NONE,
    RESET_OK,
    RESET_TOO_OLD,
    AdminActionsService,
)
from bot.services.scoring import PERIOD_ALL, ScoringService

CHAT_ID = -1500
GROUP = Chat(id=CHAT_ID, type="group", title="Admin group")
ADMIN = User(id=1, is_bot=False, first_name="Admin")
MEMBER = User(id=2, is_bot=False, first_name="Member")
OTHER = User(id=3, is_bot=False, first_name="Other")
BOT_USER = User(id=42, is_bot=True, first_name="Bot")


@pytest.fixture(autouse=True)
def _clear_refusal_guard() -> None:
    """Отказ «не админ» показывается раз в минуту: между тестами его сбросим."""
    admin_router._last_refusal.clear()


def _settings(**overrides: object) -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None, **overrides)


def _admins(status: str) -> AdminCache:
    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=status))
    return AdminCache(bot)


def _message(user: User, text: str = "/reset_scores") -> Message:
    return Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=GROUP,
        from_user=user,
        text=text,
    )


def _callback(user: User, data: str) -> CallbackQuery:
    return CallbackQuery(
        id="cb-admin",
        from_user=user,
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=11,
            date=datetime.now(UTC),
            chat=GROUP,
            from_user=BOT_USER,
            text="…",
        ),
    )


async def _chat(session_factory: async_sessionmaker[AsyncSession]) -> ChatRow:
    async with session_factory() as session:
        async with session.begin():
            return await ChatRepo(session).get_or_create(CHAT_ID, title="Admin group")


async def _member(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, name: str
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await MemberRepo(session).get_or_create(CHAT_ID, user_id, display_name=name)


async def _event(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    points: int,
    *,
    source: str = "quiz",
    at: datetime | None = None,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            event = await ScoreRepo(session).add_event(
                CHAT_ID, user_id, points=points, source=source
            )
            if at is not None:
                event.created_at = at


async def _all_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[ScoreEvent]:
    async with session_factory() as session:
        result = await session.execute(select(ScoreEvent).order_by(ScoreEvent.id))
        return list(result.scalars().all())


# --- 10.1 /stop: только админ ---


async def test_stop_is_refused_for_regular_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    sender = AsyncMock()
    games = AsyncMock()

    await stop_command(
        _message(MEMBER, "/stop"),
        chat=chat,
        lang="en",
        sender=sender,
        admins=_admins(ChatMemberStatus.MEMBER),
        games=games,
        session_factory=session_factory,
    )

    games.stop.assert_not_awaited()
    assert sender.reply.await_args.args[1] == en.TEXTS["common.not_admin"]


async def test_stop_by_admin_stops_game_and_logs_action(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    sender = AsyncMock()
    games = AsyncMock()
    games.stop = AsyncMock(return_value=True)

    await stop_command(
        _message(ADMIN, "/stop"),
        chat=chat,
        lang="en",
        sender=sender,
        admins=_admins(ChatMemberStatus.ADMINISTRATOR),
        games=games,
        session_factory=session_factory,
    )

    games.stop.assert_awaited_once_with(CHAT_ID)
    assert sender.reply.await_args.args[1] == en.TEXTS["game.stopped"]

    actions = await AdminActionsService(session_factory).recent_actions(CHAT_ID)
    assert [action.action for action in actions] == ["stop_game"]
    assert actions[0].user_id == ADMIN.id


async def test_stop_without_active_game_reports_nothing_to_stop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    sender = AsyncMock()
    games = AsyncMock()
    games.stop = AsyncMock(return_value=False)

    await stop_command(
        _message(ADMIN, "/stop"),
        chat=chat,
        lang="en",
        sender=sender,
        admins=_admins(ChatMemberStatus.ADMINISTRATOR),
        games=games,
        session_factory=session_factory,
    )

    assert sender.reply.await_args.args[1] == en.TEXTS["admin.stop_no_game"]
    assert await AdminActionsService(session_factory).recent_actions(CHAT_ID) == []


# --- 10.2 сброс рейтинга: подтверждение, отмена, откат ---


async def test_reset_confirmation_is_refused_for_regular_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    sender = AsyncMock()

    await reset_scores_command(
        _message(MEMBER),
        chat=chat,
        lang="en",
        sender=sender,
        admins=_admins(ChatMemberStatus.MEMBER),
    )

    assert sender.reply.await_args.args[1] == en.TEXTS["common.not_admin"]


async def test_reset_needs_confirmation_button(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    sender = AsyncMock()

    await reset_scores_command(
        _message(ADMIN),
        chat=chat,
        lang="en",
        sender=sender,
        admins=_admins(ChatMemberStatus.CREATOR),
    )

    markup = sender.reply.await_args.kwargs["reply_markup"]
    assert [b.callback_data for b in markup.inline_keyboard[0]] == [
        "reset:confirm",
        "reset:cancel",
    ]


async def test_reset_stores_cancellation_and_undo_restores(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    await _member(session_factory, MEMBER.id, "Member")
    await _member(session_factory, OTHER.id, "Other")
    await _event(session_factory, MEMBER.id, 30)
    await _event(session_factory, OTHER.id, 20)
    service = AdminActionsService(session_factory)
    scoring = ScoringService(session_factory, _settings())

    outcome = await service.reset_scores(CHAT_ID, ADMIN.id)
    assert outcome.result == RESET_OK
    assert (outcome.users, outcome.points) == (2, 50)

    # Исходные начисления не удалены: к ним добавились отмены.
    events = await _all_events(session_factory)
    assert [event.source for event in events] == ["quiz", "quiz", "reset", "reset"]
    assert [event.points for event in events] == [30, 20, -30, -20]
    assert all(event.reset_id is not None for event in events[2:])

    # Сброшенный рейтинг пуст. Скрытые из рейтинга тут не при чём: сумма ноль.
    assert await scoring.top(await _chat(session_factory), PERIOD_ALL) == []

    restored = await service.undo_reset(CHAT_ID, ADMIN.id)
    assert restored.result == RESET_OK
    assert restored.points == 50
    top = await scoring.top(await _chat(session_factory), PERIOD_ALL)
    assert {entry.user_id: entry.points for entry in top} == {MEMBER.id: 30, OTHER.id: 20}

    actions = await service.recent_actions(CHAT_ID)
    assert [action.action for action in actions] == ["undo_reset", "reset_scores"]


async def test_reset_without_scores_does_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    service = AdminActionsService(session_factory)

    outcome = await service.reset_scores(CHAT_ID, ADMIN.id)
    assert outcome.result == RESET_EMPTY
    assert await service.recent_actions(CHAT_ID) == []


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def test_reset_undo_refused_after_24_hours(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = _Clock(datetime(2026, 10, 25, 12, 0, tzinfo=UTC))
    await _chat(session_factory)
    await _event(session_factory, MEMBER.id, 30)
    service = AdminActionsService(session_factory, now=clock, undo_hours=24)

    await service.reset_scores(CHAT_ID, ADMIN.id)
    clock.now += timedelta(hours=25)

    outcome = await service.undo_reset(CHAT_ID, ADMIN.id)
    assert outcome.result == RESET_TOO_OLD
    # Отмена осталась в силе: очки не вернулись.
    async with session_factory() as session:
        assert await ScoreRepo(session).total(CHAT_ID, MEMBER.id) == 0


async def test_undo_without_reset_reports_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    outcome = await AdminActionsService(session_factory).undo_reset(CHAT_ID, ADMIN.id)
    assert outcome.result == RESET_NONE


async def test_reset_callback_confirm_then_undo(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    await _event(session_factory, MEMBER.id, 40)
    sender = AsyncMock()
    admins = _admins(ChatMemberStatus.ADMINISTRATOR)

    await reset_callback(
        _callback(ADMIN, "reset:confirm"),
        chat=await _chat(session_factory),
        lang="en",
        sender=sender,
        admins=admins,
        session_factory=session_factory,
        settings=_settings(),
    )
    text = sender.edit.await_args.args[2]
    assert "40" in text and "1 players" in text
    markup = sender.edit.await_args.kwargs["reply_markup"]
    assert [b.callback_data for b in markup.inline_keyboard[0]] == ["reset:undo"]

    await reset_callback(
        _callback(ADMIN, "reset:undo"),
        chat=await _chat(session_factory),
        lang="en",
        sender=sender,
        admins=admins,
        session_factory=session_factory,
        settings=_settings(),
    )
    assert "40" in sender.edit.await_args.args[2]


async def test_reset_callback_refused_for_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    await _event(session_factory, MEMBER.id, 40)
    sender = AsyncMock()

    await reset_callback(
        _callback(MEMBER, "reset:confirm"),
        chat=await _chat(session_factory),
        lang="en",
        sender=sender,
        admins=_admins(ChatMemberStatus.MEMBER),
        session_factory=session_factory,
        settings=_settings(),
    )

    sender.edit.assert_not_awaited()
    assert sender.answer_callback.await_args.args[1] == en.TEXTS["common.not_admin"]
    async with session_factory() as session:
        assert await ScoreRepo(session).total(CHAT_ID, MEMBER.id) == 40


async def test_daily_cap_counts_only_earned_points_after_reset(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Отмена сброса не снижает дневной лимит: считать нужно заработанное."""
    # Часы берём настоящие: начисление помечает событие реальным UTC,
    # и фиксированная дата в прошлом выпадала бы из дневного окна.
    now = datetime.now(UTC)
    await _chat(session_factory)
    # Вчерашние очки: в дневное окно не попадают.
    await _event(session_factory, MEMBER.id, 300, at=now - timedelta(days=1))
    service = AdminActionsService(session_factory, now=lambda: now)
    await service.reset_scores(CHAT_ID, ADMIN.id)

    scoring = ScoringService(session_factory, _settings(score_daily_cap=300), now=lambda: now)
    assert await scoring.award(CHAT_ID, MEMBER.id, 300, "quiz") == 300
    # Лимит уже исчерпан новыми очками — второй раз начислять нечего.
    assert await scoring.award(CHAT_ID, MEMBER.id, 300, "quiz") == 0


# --- 10.3 приватность: скрыться из рейтинга и вернуться ---


async def test_privacy_hides_and_shows_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory)
    await _member(session_factory, MEMBER.id, "Member")
    await _event(session_factory, MEMBER.id, 25)
    sender = AsyncMock()
    chat = await _chat(session_factory)

    await privacy_command(
        _message(MEMBER, "/privacy"),
        chat=chat,
        lang="en",
        sender=sender,
        session_factory=session_factory,
    )
    markup = sender.reply.await_args.kwargs["reply_markup"]
    assert [b.callback_data for b in markup.inline_keyboard[0]] == ["privacy:hide"]

    await privacy_callback(
        _callback(MEMBER, "privacy:hide"),
        chat=chat,
        lang="en",
        sender=sender,
        session_factory=session_factory,
    )
    async with session_factory() as session:
        member = await MemberRepo(session).get(CHAT_ID, MEMBER.id)
    assert member is not None and member.opted_out is True
    assert sender.answer_callback.await_args.args[1] == en.TEXTS["privacy.hidden"]

    # Скрытый участник не виден в рейтинге...
    scoring = ScoringService(session_factory, _settings())
    assert await scoring.top(chat, PERIOD_ALL) == []
    # ...но очки у него остаются и он может играть дальше.
    await _event(session_factory, MEMBER.id, 10)
    async with session_factory() as session:
        assert await ScoreRepo(session).total(CHAT_ID, MEMBER.id) == 35

    # Возврат в рейтинг: снова виден.
    await privacy_callback(
        _callback(MEMBER, "privacy:show"),
        chat=chat,
        lang="en",
        sender=sender,
        session_factory=session_factory,
    )
    top = await scoring.top(chat, PERIOD_ALL)
    assert [(entry.user_id, entry.points) for entry in top] == [(MEMBER.id, 35)]
    actions = await AdminActionsService(session_factory).recent_actions(CHAT_ID)
    assert [action.action for action in actions] == ["opt_in", "opt_out"]
