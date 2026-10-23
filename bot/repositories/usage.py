"""Репозиторий дневных квот запросов к ИИ."""

from datetime import date

from bot.models import AIUsage
from bot.repositories.base import BaseRepo


class UsageRepo(BaseRepo):
    """Доступ к таблице `ai_usage`."""

    async def get_requests(self, chat_id: int, on_date: date) -> int:
        """Сколько запросов к ИИ чат сделал за день."""
        usage = await self.session.get(AIUsage, (chat_id, on_date))
        return usage.requests if usage is not None else 0

    async def increment(self, chat_id: int, on_date: date, *, tokens: int = 0) -> int:
        """Учитывает запрос к ИИ; возвращает новое число запросов за день."""
        usage = await self.session.get(AIUsage, (chat_id, on_date))
        if usage is None:
            usage = AIUsage(chat_id=chat_id, date=on_date, requests=1, tokens=tokens)
            self.session.add(usage)
        else:
            usage.requests += 1
            usage.tokens += tokens
        await self.session.flush()
        return usage.requests
