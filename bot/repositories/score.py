"""Репозиторий журнала очков."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select

from bot.models import Member, ScoreEvent
from bot.repositories.base import BaseRepo


@dataclass(frozen=True)
class LeaderboardEntry:
    """Строка рейтинга: участник и его сумма очков за период."""

    user_id: int
    points: int
    display_name: str | None
    # Время последнего начисления за период — им разрешаются ничьи.
    last_event_at: datetime


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
    ) -> list[LeaderboardEntry]:
        """Топ участников по сумме очков за период.

        При равенстве очков выше тот, кто набрал их раньше (по времени
        последнего события). Если время совпало с точностью часов
        (разрешение системных часов), порядок задаёт номер события:
        меньший номер записан раньше. Участники с opted_out не видны.
        """
        points_sum = func.sum(ScoreEvent.points).label("points")
        last_event = func.max(ScoreEvent.created_at).label("last_event_at")
        last_id = func.max(ScoreEvent.id).label("last_id")
        stmt = (
            select(ScoreEvent.user_id, points_sum, Member.display_name, last_event)
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
            stmt.group_by(ScoreEvent.user_id, Member.display_name)
            .order_by(points_sum.desc(), last_event.asc(), last_id.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [
            LeaderboardEntry(
                user_id=user_id,
                points=int(points),
                display_name=display_name,
                last_event_at=last_event_at,
            )
            for user_id, points, display_name, last_event_at in result.all()
        ]

    async def place(
        self,
        chat_id: int,
        user_id: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int | None:
        """Место участника в рейтинге за период.

        Ничьи разрешаются так же, как в `leaderboard`: выше тот, кто
        набрал очки раньше. Нет очков за период или участник скрыт
        (`opted_out`) — места нет (None).
        """
        totals = self._totals_subquery(chat_id, since=since, until=until)
        mine = await self.session.execute(
            select(totals.c.points, totals.c.last_event_at, totals.c.last_id).where(
                totals.c.user_id == user_id
            )
        )
        row = mine.first()
        if row is None:
            return None
        points, last_event_at, last_id = row
        ahead = await self.session.execute(
            select(func.count())
            .select_from(totals)
            .where(
                or_(
                    totals.c.points > points,
                    and_(
                        totals.c.points == points,
                        totals.c.last_event_at < last_event_at,
                    ),
                    and_(
                        totals.c.points == points,
                        totals.c.last_event_at == last_event_at,
                        totals.c.last_id < last_id,
                    ),
                )
            )
        )
        return int(ahead.scalar_one()) + 1

    def _totals_subquery(
        self,
        chat_id: int,
        *,
        since: datetime | None,
        until: datetime | None,
    ):
        """Подзапрос «сумма и последнее событие» по видимым участникам чата."""
        points_sum = func.sum(ScoreEvent.points).label("points")
        last_event = func.max(ScoreEvent.created_at).label("last_event_at")
        last_id = func.max(ScoreEvent.id).label("last_id")
        stmt = (
            select(ScoreEvent.user_id, points_sum, last_event, last_id)
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
        return stmt.group_by(ScoreEvent.user_id).subquery()
