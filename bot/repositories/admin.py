"""Репозиторий журнала действий администраторов и участников."""

from sqlalchemy import select

from bot.models import AdminAction
from bot.repositories.base import BaseRepo


class AdminActionRepo(BaseRepo):
    """Доступ к таблице `admin_actions`."""

    async def log(
        self,
        chat_id: int,
        user_id: int,
        action: str,
        *,
        details: dict | None = None,
    ) -> AdminAction:
        """Записывает действие: кто, что и когда сделал в группе."""
        row = AdminAction(chat_id=chat_id, user_id=user_id, action=action, details=details)
        self.session.add(row)
        await self.session.flush()
        return row

    async def recent(self, chat_id: int, *, limit: int = 20) -> list[AdminAction]:
        """Последние действия чата — новые первыми."""
        stmt = (
            select(AdminAction)
            .where(AdminAction.chat_id == chat_id)
            .order_by(AdminAction.created_at.desc(), AdminAction.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
