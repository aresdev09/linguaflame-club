"""Репозиторий журнала автосообщений планировщика."""

from datetime import date, datetime

from sqlalchemy import func, select

from bot.models import ScheduledMessage, utcnow
from bot.repositories.base import BaseRepo


class SchedulerRepo(BaseRepo):
    """Доступ к таблице `scheduled_messages`.

    Журнал решает две задачи: уникальный индекс (чат, дата, вид) не даёт
    отправить одно и то же сообщение дважды, а число записей за день
    ограничивает автосообщения на чат.
    """

    async def get(self, chat_id: int, on_date: date, kind: str) -> ScheduledMessage | None:
        """Запись автосообщения за день, если она уже создана."""
        stmt = select(ScheduledMessage).where(
            ScheduledMessage.chat_id == chat_id,
            ScheduledMessage.date == on_date,
            ScheduledMessage.kind == kind,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create(
        self,
        chat_id: int,
        on_date: date,
        kind: str,
        *,
        lease_until: datetime | None = None,
    ) -> ScheduledMessage:
        """Создаёт запись до отправки: повторный tick её же и найдёт.

        `lease_until` — «замок» на время отправки: пока он в будущем,
        второй проход планировщика это сообщение не тронет.
        """
        message = ScheduledMessage(
            chat_id=chat_id,
            date=on_date,
            kind=kind,
            next_attempt_at=lease_until,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def count(self, chat_id: int, on_date: date) -> int:
        """Сколько автосообщений уже заведено на этот день чата."""
        stmt = (
            select(func.count())
            .select_from(ScheduledMessage)
            .where(
                ScheduledMessage.chat_id == chat_id,
                ScheduledMessage.date == on_date,
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def mark_sent(
        self,
        message_id: int,
        telegram_message_id: int | None,
        *,
        at: datetime | None = None,
    ) -> None:
        """Отмечает автосообщение отправленным.

        `at` — время планировщика: с `SCHEDULER_TIME_OFFSET` оно отличается
        от системного, и в журнале нужна та же шкала, что у `date`.
        """
        message = await self.session.get(ScheduledMessage, message_id)
        if message is None:
            return
        message.message_id = telegram_message_id
        message.posted_at = at or utcnow()
        message.next_attempt_at = None
        await self.session.flush()

    async def postpone(self, message_id: int, *, next_attempt_at: datetime) -> int:
        """Считает попытку неудачной и переносит повтор; возвращает счётчик."""
        message = await self.session.get(ScheduledMessage, message_id)
        if message is None:
            return 0
        message.attempts += 1
        message.next_attempt_at = next_attempt_at
        await self.session.flush()
        return message.attempts
