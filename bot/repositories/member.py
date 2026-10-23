"""Репозиторий участников групп."""

from bot.models import Member, utcnow
from bot.repositories.base import BaseRepo


class MemberRepo(BaseRepo):
    """Доступ к таблице `members`."""

    async def get(self, chat_id: int, user_id: int) -> Member | None:
        return await self.session.get(Member, (chat_id, user_id))

    async def get_or_create(
        self,
        chat_id: int,
        user_id: int,
        *,
        display_name: str,
        username: str | None = None,
    ) -> Member:
        """Создаёт участника при первом действии, обновляет визит."""
        member = await self.get(chat_id, user_id)
        now = utcnow()
        if member is None:
            member = Member(
                chat_id=chat_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(member)
        else:
            member.username = username
            member.display_name = display_name
            member.last_seen_at = now
        await self.session.flush()
        return member

    async def set_opted_out(self, chat_id: int, user_id: int, opted_out: bool) -> None:
        """Скрывает участника из рейтинга или возвращает его обратно."""
        member = await self.get(chat_id, user_id)
        if member is None:
            return
        member.opted_out = opted_out
        await self.session.flush()
