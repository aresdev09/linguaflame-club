"""Репозиторий игровых сессий и ответов."""

import uuid
from datetime import datetime

from sqlalchemy import select

from bot.models import GameAnswer, GameSession, utcnow
from bot.repositories.base import BaseRepo


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

    async def get(self, session_id: uuid.UUID) -> GameSession | None:
        return await self.session.get(GameSession, session_id)

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
