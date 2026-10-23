"""Репозиторий журнала очков."""

import uuid
from datetime import datetime

from sqlalchemy import and_, func, select

from bot.models import Member, ScoreEvent
from bot.repositories.base import BaseRepo


class ScoreRepo(BaseRepo):
    """Доступ к журналу `score_events` — источнику правды для рейтинга."""

    async def add_event(
        self,
        chat_id: int,
        user_id: int,
        *,
        points: int,
        source: str,
        session_id: uuid.UUID | None = None,
    ) -> ScoreEvent:
        """Записывает начисление очков."""
        event = ScoreEvent(
            chat_id=chat_id,
            user_id=user_id,
            points=points,
            source=source,
            session_id=session_id,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def total(
        self,
        chat_id: int,
        user_id: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """Сумма очков участника за период (без границ — за всё время)."""
        stmt = select(func.coalesce(func.sum(ScoreEvent.points), 0)).where(
            ScoreEvent.chat_id == chat_id,
            ScoreEvent.user_id == user_id,
        )
        if since is not None:
            stmt = stmt.where(ScoreEvent.created_at >= since)
        if until is not None:
            stmt = stmt.where(ScoreEvent.created_at < until)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def leaderboard(
        self,
        chat_id: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 10,
    ) -> list[tuple[int, int]]:
        """Топ участников по сумме очков: (user_id, points).

        При равенстве очков выше тот, кто набрал их раньше (по времени
        последнего события). Участники с opted_out не показываются.
        """
        points_sum = func.sum(ScoreEvent.points)
        last_event = func.max(ScoreEvent.created_at)
        stmt = (
            select(ScoreEvent.user_id, points_sum)
            .outerjoin(
                Member,
                and_(
                    Member.chat_id == ScoreEvent.chat_id,
                    Member.user_id == ScoreEvent.user_id,
                ),
            )
            .where(ScoreEvent.chat_id == chat_id)
            .where(func.coalesce(Member.opted_out, False).is_(False))
        )
        if since is not None:
            stmt = stmt.where(ScoreEvent.created_at >= since)
        if until is not None:
            stmt = stmt.where(ScoreEvent.created_at < until)
        stmt = (
            stmt.group_by(ScoreEvent.user_id)
            .order_by(points_sum.desc(), last_event.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(user_id, int(points)) for user_id, points in result.all()]
