"""Действия администраторов: сброс рейтинга, откат и журнал.

Сброс рейтинга хранится как отмена, а не удаление: каждому участнику
пишется отрицательное событие `score_events` (источник `reset`),
привязанное к записи `score_resets`. Пока сброс свежий (по умолчанию
24 часа), его можно вернуть — события отмены удаляются, а исходные
начисления остаются нетронутыми. Кто и что сделал, пишется в
`admin_actions`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.models import AdminAction, utcnow
from bot.repositories import AdminActionRepo, ScoreRepo

logger = logging.getLogger(__name__)

# Результаты сброса и отката.
RESET_OK = "ok"
RESET_EMPTY = "empty"  # сбрасывать нечего: очков нет
RESET_NONE = "none"  # нет сброса, который можно вернуть
RESET_TOO_OLD = "too_old"  # сброс старше окна отката


@dataclass(frozen=True)
class ResetOutcome:
    """Итог сброса или отката: что получилось и сколько очков затронуто."""

    result: str
    users: int = 0
    points: int = 0

    @property
    def ok(self) -> bool:
        return self.result == RESET_OK


def as_utc(moment: datetime) -> datetime:
    """SQLite отдаёт время без зоны: такое время считаем UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


class AdminActionsService:
    """Сброс и откат рейтинга плюс журнал действий администраторов."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        now: Callable[[], datetime] = utcnow,
        undo_hours: int = 24,
    ) -> None:
        self._session_factory = session_factory
        self._now = now
        self._undo_window = timedelta(hours=undo_hours)

    async def log(
        self,
        chat_id: int,
        user_id: int,
        action: str,
        *,
        details: dict | None = None,
    ) -> None:
        """Записывает действие в журнал: кто, что и когда сделал."""
        async with self._session_factory() as session:
            async with session.begin():
                await AdminActionRepo(session).log(
                    chat_id, user_id, action, details=details
                )

    async def recent_actions(
        self, chat_id: int, *, limit: int = 20
    ) -> list[AdminAction]:
        """Последние действия чата — новые первыми."""
        async with self._session_factory() as session:
            return await AdminActionRepo(session).recent(chat_id, limit=limit)

    async def reset_scores(self, chat_id: int, admin_user_id: int) -> ResetOutcome:
        """Обнуляет рейтинг группы, сохраняя отмену для возможного отката."""
        async with self._session_factory() as session:
            async with session.begin():
                scores = ScoreRepo(session)
                totals = await scores.totals_by_user(chat_id)
                positives = {user: points for user, points in totals.items() if points > 0}
                if not positives:
                    return ResetOutcome(RESET_EMPTY)

                reset = await scores.create_reset(
                    chat_id, admin_user_id, created_at=self._now()
                )
                cancelled = 0
                for user_id, points in positives.items():
                    await scores.add_event(
                        chat_id,
                        user_id,
                        points=-points,
                        source="reset",
                        reset_id=reset.id,
                    )
                    cancelled += points
                await AdminActionRepo(session).log(
                    chat_id,
                    admin_user_id,
                    "reset_scores",
                    details={
                        "reset_id": reset.id,
                        "users": len(positives),
                        "points": cancelled,
                    },
                )
        logger.info(
            "Чат %s: рейтинг сброшен админом %s (%d участников, %d очков)",
            chat_id,
            admin_user_id,
            len(positives),
            cancelled,
        )
        return ResetOutcome(RESET_OK, users=len(positives), points=cancelled)

    async def undo_reset(self, chat_id: int, admin_user_id: int) -> ResetOutcome:
        """Возвращает рейтинг: удаляет отмену, если сброс свежий."""
        now = self._now()
        async with self._session_factory() as session:
            async with session.begin():
                scores = ScoreRepo(session)
                reset = await scores.latest_active_reset(chat_id)
                if reset is None:
                    return ResetOutcome(RESET_NONE)
                if now - as_utc(reset.created_at) > self._undo_window:
                    return ResetOutcome(RESET_TOO_OLD)
                restored = await scores.reset_cancelled_points(reset.id)
                await scores.delete_reset_events(reset.id)
                await scores.mark_reset_undone(reset.id)
                await AdminActionRepo(session).log(
                    chat_id,
                    admin_user_id,
                    "undo_reset",
                    details={"reset_id": reset.id, "points": restored},
                )
        logger.info(
            "Чат %s: сброс рейтинга отменён админом %s (%d очков)",
            chat_id,
            admin_user_id,
            restored,
        )
        return ResetOutcome(RESET_OK, points=restored)
