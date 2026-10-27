"""Тесты уборки данных (этап 10): тексты ответов и удалённые чаты.

Проверяются сроки хранения: тексты ответов игр живут 7 дней (сами
ответы и статистика остаются), а данные группы, из которой бота
удалили, стираются через 30 дней.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import GameAnswer, GameSession
from bot.repositories import ChatRepo, GameRepo, MemberRepo, ScoreRepo
from bot.services.maintenance import MaintenanceService

OLD_CHAT = -1601
RECENT_CHAT = -1602
ACTIVE_CHAT = -1603
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None, **overrides)


async def _chat(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    *,
    active: bool = True,
    deactivated_at: datetime | None = None,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            repo = ChatRepo(session)
            await repo.get_or_create(chat_id, title="Group")
            await repo.update_settings(chat_id, active=active, deactivated_at=deactivated_at)


async def _answer(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    *,
    at: datetime,
    text: str,
) -> None:
    """Создаёт закрытую игру с одним ответом заданного времени."""
    async with session_factory() as session:
        async with session.begin():
            repo = GameRepo(session)
            game = await repo.create_session(chat_id, kind="quiz", started_by=1)
            answer = await repo.add_answer(
                game.id,
                1,
                user_id=1,
                answer=text,
                is_correct=True,
                answer_time_ms=1000,
                points=10,
            )
            answer.created_at = at
            await repo.close(game.id, "finished")


async def test_answer_texts_are_cleared_but_stats_kept(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory, OLD_CHAT)
    service = MaintenanceService(session_factory, _settings(), now=lambda: NOW)
    await _answer(session_factory, OLD_CHAT, at=NOW - timedelta(days=8), text="old-secret")
    await _answer(session_factory, OLD_CHAT, at=NOW - timedelta(days=2), text="fresh")

    cleared = await service.purge_answer_texts()
    assert cleared == 1

    async with session_factory() as session:
        answers = list(
            (await session.execute(select(GameAnswer).order_by(GameAnswer.id))).scalars().all()
        )
    assert answers[0].answer == ""  # старый текст стёрт
    assert answers[1].answer == "fresh"  # свежий ответ не тронут

    # Статистика сохранена: ответы и верные ответы считаются по-прежнему.
    async with session_factory() as session:
        total, correct = await GameRepo(session).answer_stats(OLD_CHAT, 1)
    assert (total, correct) == (2, 2)


async def test_inactive_chat_data_is_deleted_after_30_days(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory, OLD_CHAT, active=False, deactivated_at=NOW - timedelta(days=31))
    await _chat(
        session_factory,
        RECENT_CHAT,
        active=False,
        deactivated_at=NOW - timedelta(days=5),
    )
    await _chat(session_factory, ACTIVE_CHAT, active=True)

    # У старого чата есть данные всех видов: они должны исчезнуть вместе с ним.
    async with session_factory() as session:
        async with session.begin():
            await MemberRepo(session).get_or_create(OLD_CHAT, 5, display_name="Anna")
            await ScoreRepo(session).add_event(OLD_CHAT, 5, points=10, source="quiz")
    await _answer(session_factory, OLD_CHAT, at=NOW - timedelta(days=10), text="answer")

    async with session_factory() as session:
        game_ids = list(
            (await session.execute(select(GameSession.id).where(GameSession.chat_id == OLD_CHAT)))
            .scalars()
            .all()
        )
    assert game_ids  # игра записана

    service = MaintenanceService(session_factory, _settings(), now=lambda: NOW)
    removed = await service.purge_inactive_chats()
    assert removed == 1

    async with session_factory() as session:
        assert await ChatRepo(session).get(OLD_CHAT) is None
        assert await MemberRepo(session).get(OLD_CHAT, 5) is None
        assert await ScoreRepo(session).total(OLD_CHAT, 5) == 0
        assert (
            await session.execute(select(GameSession).where(GameSession.chat_id == OLD_CHAT))
        ).scalars().first() is None
        assert (
            await session.execute(select(GameAnswer).where(GameAnswer.session_id.in_(game_ids)))
        ).scalars().first() is None
        # Недавний неактивный и активный чаты остаются нетронутыми.
        assert await ChatRepo(session).get(RECENT_CHAT) is not None
        assert await ChatRepo(session).get(ACTIVE_CHAT) is not None


async def test_run_reports_both_cleanups(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _chat(session_factory, OLD_CHAT, active=False, deactivated_at=NOW - timedelta(days=40))
    await _answer(session_factory, OLD_CHAT, at=NOW - timedelta(days=10), text="x")

    service = MaintenanceService(session_factory, _settings(), now=lambda: NOW)
    answers, chats = await service.run()

    assert (answers, chats) == (1, 1)
