"""Тесты middleware: контекст группы и кулдауны команд."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import GROUP_ANONYMOUS_BOT_ID
from bot.middlewares.group import GroupContextMiddleware
from bot.middlewares.throttle import CooldownTracker, ThrottleMiddleware
from bot.repositories import ChatRepo, MemberRepo

GROUP = Chat(id=-100, type="group", title="Study group")
USER = User(id=5, is_bot=False, first_name="Anna", username="anna")

_update_counter = 0


def _update(
    chat: Chat,
    user: User | None = None,
    text: str | None = None,
    **message_kwargs: object,
) -> Update:
    global _update_counter
    _update_counter += 1
    message = Message(
        message_id=_update_counter,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
        **message_kwargs,
    )
    return Update(update_id=_update_counter, message=message)


async def _run_context(
    middleware: GroupContextMiddleware, update: Update
) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def handler(event: Update, data: dict[str, object]) -> None:
        captured.update(data)

    await middleware(handler, update, {})
    return captured


async def test_context_loads_chat_and_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    middleware = GroupContextMiddleware(session_factory)

    captured = await _run_context(middleware, _update(GROUP, USER, "/setup"))

    assert captured["chat"].id == -100
    assert captured["lang"] == "en"
    assert captured["member"].user_id == 5

    async with session_factory() as session:
        stored_chat = await ChatRepo(session).get(-100)
        assert stored_chat is not None
        stored_member = await MemberRepo(session).get(-100, 5)
        assert stored_member is not None
        assert stored_member.display_name == "Anna"


async def test_context_refreshes_member_profile(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    middleware = GroupContextMiddleware(session_factory)
    await _run_context(middleware, _update(GROUP, USER, "/top"))

    renamed = User(id=5, is_bot=False, first_name="Anna", last_name="Kim")
    await _run_context(middleware, _update(GROUP, renamed, "/top"))

    async with session_factory() as session:
        member = await MemberRepo(session).get(-100, 5)
        assert member is not None
        assert member.display_name == "Anna Kim"


async def test_context_skips_private_chats(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    middleware = GroupContextMiddleware(session_factory)
    private = Chat(id=5, type="private", first_name="Anna")

    captured = await _run_context(middleware, _update(private, USER, "/start"))

    assert "chat" not in captured
    async with session_factory() as session:
        assert await ChatRepo(session).get(5) is None


async def test_context_skips_migration_messages(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    middleware = GroupContextMiddleware(session_factory)
    supergroup = Chat(id=-500, type="supergroup", title="G")

    captured = await _run_context(
        middleware, _update(supergroup, None, migrate_from_chat_id=-100)
    )

    assert "chat" not in captured
    async with session_factory() as session:
        assert await ChatRepo(session).get(-500) is None


async def test_context_does_not_track_anonymous_admins(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    middleware = GroupContextMiddleware(session_factory)
    anon = User(id=GROUP_ANONYMOUS_BOT_ID, is_bot=True, first_name="Group")

    captured = await _run_context(middleware, _update(GROUP, anon, "/setup"))

    assert captured["chat"].id == -100
    assert "member" not in captured


def test_cooldown_tracker_allows_and_blocks() -> None:
    current = [0.0]
    tracker = CooldownTracker(monotonic=lambda: current[0])

    assert tracker.allow("key", 3.0) is True
    assert tracker.allow("key", 3.0) is False
    current[0] = 2.9
    assert tracker.allow("key", 3.0) is False
    current[0] = 3.0
    assert tracker.allow("key", 3.0) is True


async def test_throttle_blocks_second_command_from_same_user() -> None:
    middleware = ThrottleMiddleware(user_cooldown=3.0, heavy_cooldown=10.0)
    handler = AsyncMock()
    data: dict[str, object] = {"chat": SimpleNamespace(id=-100)}

    await middleware(handler, _update(GROUP, USER, "/top"), data)
    await middleware(handler, _update(GROUP, USER, "/top"), data)

    assert handler.await_count == 1


async def test_heavy_command_cooldown_shared_by_chat() -> None:
    middleware = ThrottleMiddleware(user_cooldown=0.0, heavy_cooldown=10.0)
    handler = AsyncMock()
    data: dict[str, object] = {"chat": SimpleNamespace(id=-100)}
    other_user = User(id=6, is_bot=False, first_name="Bek")

    await middleware(handler, _update(GROUP, USER, "/quiz"), data)
    await middleware(handler, _update(GROUP, other_user, "/quiz"), data)

    assert handler.await_count == 1


async def test_throttle_ignores_updates_without_group_context() -> None:
    """В личке и служебных событиях кулдауны не действуют."""
    middleware = ThrottleMiddleware(user_cooldown=3.0, heavy_cooldown=10.0)
    handler = AsyncMock()

    await middleware(handler, _update(GROUP, USER, "/top"), {})
    await middleware(handler, _update(GROUP, USER, "/top"), {})

    assert handler.await_count == 2
