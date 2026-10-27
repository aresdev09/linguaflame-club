"""Репозиторий игровых сессий и ответов."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, func, select

from bot.models import GameAnswer, GameSession, Member, utcnow
from bot.repositories.base import BaseRepo


@dataclass(frozen=True)
class RoundAnswer:
    """Ответ раунда: участник и верность (имя нужно для итогов раунда)."""

    user_id: int
    is_correct: bool
    display_name: str | None


@dataclass(frozen=True)
class SessionTotal:
    """Очки участника за сессию; `first_answer_id` решает ничьи."""

    user_id: int
    points: int
    display_name: str | None
    first_answer_id: int


@dataclass(frozen=True)
class AccuracyEntry:
    """Точность ответов участника за период — для «Лучший по точности»."""

    user_id: int
    answers: int
    correct: int
    display_name: str | None

    @property
    def accuracy(self) -> int:
        """Процент верных ответов, целое число."""
        if self.answers <= 0:
            return 0
        return round(self.correct * 100 / self.answers)


class GameRepo(BaseRepo):
    """Доступ к таблицам `game_sessions` и `game_answers`."""

    async def create_session(
        self,
        chat_id: int,
        *,
        kind: str,
        started_by: int,
        state: dict | None = None,
        ends_at: datetime | None = None,
        message_id: int | None = None,
    ) -> GameSession:
        """Создаёт сессию игры.

        Вторая активная игра в том же чате отклоняется частичным
        уникальным индексом на уровне базы данных.
        """
        session = GameSession(
            chat_id=chat_id,
            kind=kind,
            started_by=started_by,
            state=state or {},
            ends_at=ends_at,
            message_id=message_id,
        )
        self.session.add(session)
        await self.session.flush()
        return session

    async def get(self, session_id: uuid.UUID, *, for_update: bool = False) -> GameSession | None:
        """Сессия по id; `for_update` блокирует строку до конца транзакции.

        Под этой блокировкой считаются верные ответы раунда — бонус
        «первому» не должен достаться двум участникам сразу.
        """
        return await self.session.get(GameSession, session_id, with_for_update=for_update)

    async def get_active(self, chat_id: int) -> GameSession | None:
        """Текущая незавершённая игра чата (статус `running`), если есть."""
        stmt = (
            select(GameSession)
            .where(GameSession.chat_id == chat_id, GameSession.status == "running")
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_running(self) -> list[GameSession]:
        """Все активные игры: нужны восстановлению после перезапуска бота."""
        stmt = select(GameSession).where(GameSession.status == "running")
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_message_id(self, session_id: uuid.UUID, message_id: int) -> None:
        game = await self.get(session_id)
        if game is None:
            return
        game.message_id = message_id
        await self.session.flush()

    async def update_state(self, session_id: uuid.UUID, state: dict) -> None:
        game = await self.get(session_id)
        if game is None:
            return
        game.state = state
        await self.session.flush()

    async def close(
        self,
        session_id: uuid.UUID,
        status: str,
        *,
        finished_at: datetime | None = None,
    ) -> None:
        """Закрывает сессию: finished, stopped или expired."""
        game = await self.get(session_id)
        if game is None:
            return
        game.status = status
        game.finished_at = finished_at or utcnow()
        await self.session.flush()

    async def add_answer(
        self,
        session_id: uuid.UUID,
        round: int,
        *,
        user_id: int,
        answer: str,
        is_correct: bool,
        answer_time_ms: int,
        points: int,
    ) -> GameAnswer:
        """Записывает ответ участника.

        Второй ответ того же участника в том же раунде отклоняется
        уникальным индексом на уровне базы данных.
        """
        game_answer = GameAnswer(
            session_id=session_id,
            round=round,
            user_id=user_id,
            answer=answer,
            is_correct=is_correct,
            answer_time_ms=answer_time_ms,
            points=points,
        )
        self.session.add(game_answer)
        await self.session.flush()
        return game_answer

    async def get_answer(
        self, session_id: uuid.UUID, round: int, user_id: int
    ) -> GameAnswer | None:
        """Ответ участника в раунде, если он уже отвечал."""
        stmt = select(GameAnswer).where(
            GameAnswer.session_id == session_id,
            GameAnswer.round == round,
            GameAnswer.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def answer_stats(
        self,
        chat_id: int,
        user_id: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[int, int]:
        """Сколько ответов дал участник в играх чата и сколько из них верных.

        Ответы лежат в `game_answers`, игры сессии привязывают их к чату.
        """
        correct_flag = case((GameAnswer.is_correct.is_(True), 1), else_=0)
        stmt = (
            select(func.count(GameAnswer.id), func.coalesce(func.sum(correct_flag), 0))
            .join(GameSession, GameSession.id == GameAnswer.session_id)
            .where(GameSession.chat_id == chat_id, GameAnswer.user_id == user_id)
        )
        if since is not None:
            stmt = stmt.where(GameAnswer.created_at >= since)
        if until is not None:
            stmt = stmt.where(GameAnswer.created_at < until)
        result = await self.session.execute(stmt)
        answers, correct = result.one()
        return int(answers), int(correct)

    async def accuracy_entries(
        self,
        chat_id: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        min_answers: int = 5,
        limit: int = 1,
    ) -> list[AccuracyEntry]:
        """Самые точные участники периода: не меньше `min_answers` ответов.

        Ничья решается по числу ответов: кто ответил больше, тот выше.
        Участники с `opted_out` в итоги не попадают.
        """
        correct_flag = case((GameAnswer.is_correct.is_(True), 1), else_=0)
        answers = func.count(GameAnswer.id).label("answers")
        stmt = (
            select(GameAnswer.user_id, answers, func.sum(correct_flag), Member.display_name)
            .join(GameSession, GameSession.id == GameAnswer.session_id)
            .outerjoin(
                Member,
                and_(
                    Member.chat_id == GameSession.chat_id,
                    Member.user_id == GameAnswer.user_id,
                ),
            )
            .where(GameSession.chat_id == chat_id)
            .where(func.coalesce(Member.opted_out, False).is_(False))
        )
        if since is not None:
            stmt = stmt.where(GameAnswer.created_at >= since)
        if until is not None:
            stmt = stmt.where(GameAnswer.created_at < until)
        stmt = (
            stmt.group_by(GameAnswer.user_id, Member.display_name)
            .having(func.count(GameAnswer.id) >= min_answers)
            .order_by(
                (func.sum(correct_flag) * 1.0 / func.count(GameAnswer.id)).desc(),
                answers.desc(),
                GameAnswer.user_id.asc(),
            )
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [
            AccuracyEntry(
                user_id=user_id,
                answers=int(total),
                correct=int(correct),
                display_name=display_name,
            )
            for user_id, total, correct, display_name in result.all()
        ]

    async def count_correct_answers(self, session_id: uuid.UUID, round: int) -> int:
        """Сколько верных ответов уже принято в раунде (бонус «первому»)."""
        stmt = (
            select(func.count())
            .select_from(GameAnswer)
            .where(
                GameAnswer.session_id == session_id,
                GameAnswer.round == round,
                GameAnswer.is_correct.is_(True),
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def round_answers(self, session_id: uuid.UUID, round: int) -> list[RoundAnswer]:
        """Ответы раунда с именами участников — для итогов раунда."""
        stmt = (
            select(GameAnswer.user_id, GameAnswer.is_correct, Member.display_name)
            .join(GameSession, GameSession.id == GameAnswer.session_id)
            .outerjoin(
                Member,
                and_(
                    Member.chat_id == GameSession.chat_id,
                    Member.user_id == GameAnswer.user_id,
                ),
            )
            .where(GameAnswer.session_id == session_id, GameAnswer.round == round)
            .order_by(GameAnswer.id)
        )
        result = await self.session.execute(stmt)
        return [
            RoundAnswer(user_id=user_id, is_correct=is_correct, display_name=display_name)
            for user_id, is_correct, display_name in result.all()
        ]

    async def session_totals(self, session_id: uuid.UUID) -> list[SessionTotal]:
        """Очки участников за сессию; при равенстве выше тот, кто ответил раньше."""
        points_sum = func.sum(GameAnswer.points).label("points")
        first_id = func.min(GameAnswer.id).label("first_id")
        stmt = (
            select(GameAnswer.user_id, points_sum, Member.display_name, first_id)
            .join(GameSession, GameSession.id == GameAnswer.session_id)
            .outerjoin(
                Member,
                and_(
                    Member.chat_id == GameSession.chat_id,
                    Member.user_id == GameAnswer.user_id,
                ),
            )
            .where(GameAnswer.session_id == session_id)
            .group_by(GameAnswer.user_id, Member.display_name)
        )
        result = await self.session.execute(stmt)
        totals = [
            SessionTotal(
                user_id=user_id,
                points=int(points),
                display_name=display_name,
                first_answer_id=int(first),
            )
            for user_id, points, display_name, first in result.all()
        ]
        return sorted(totals, key=lambda total: (-total.points, total.first_answer_id))

    async def count_started_since(self, chat_id: int, since: datetime) -> int:
        """Сколько игр начато в чате с указанного времени (дневной лимит)."""
        stmt = (
            select(func.count())
            .select_from(GameSession)
            .where(GameSession.chat_id == chat_id, GameSession.started_at >= since)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def latest_finished_at(self, chat_id: int) -> datetime | None:
        """Время закрытия последней игры чата (кулдаун между играми)."""
        stmt = select(func.max(GameSession.finished_at)).where(
            GameSession.chat_id == chat_id, GameSession.finished_at.is_not(None)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
