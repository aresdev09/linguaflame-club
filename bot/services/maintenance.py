"""Обслуживание данных: очистка текстов ответов и удаление старых чатов.

Задачи приватности из этапа 10:

- тексты ответов в играх хранятся не дольше 7 дней (сами ответы и
  статистика остаются, стирается только текст);
- данные группы, из которой бота удалили, удаляются через 30 дней.

Оба срока и частота уборки настраиваются в конфиге. Уборку запускает
планировщик, а также один раз при старте бота.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import utcnow
from bot.repositories import MaintenanceRepo

logger = logging.getLogger(__name__)


class MaintenanceService:
    """Уборка по расписанию: ответы игр и данные удалённых чатов."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._now = now

    async def purge_answer_texts(self) -> int:
        """Очищает тексты ответов старше срока хранения."""
        cutoff = self._now() - timedelta(days=self._settings.maintenance_answer_retention_days)
        async with self._session_factory() as session:
            async with session.begin():
                cleared = await MaintenanceRepo(session).clear_answer_texts_before(cutoff)
        if cleared:
            logger.info("Обслуживание: очищено текстов ответов — %d", cleared)
        return cleared

    async def purge_inactive_chats(self) -> int:
        """Удаляет данные чатов, удалённых из бота больше срока назад."""
        cutoff = self._now() - timedelta(days=self._settings.maintenance_chat_retention_days)
        async with self._session_factory() as session:
            chat_ids = await MaintenanceRepo(session).list_purgeable_chat_ids(cutoff)
        for chat_id in chat_ids:
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        await MaintenanceRepo(session).delete_chat_data(chat_id)
            except Exception:
                # Сбой на одном чате не должен мешать убрать остальные.
                logger.exception("Не удалось удалить данные чата %s", chat_id)
        if chat_ids:
            logger.info("Обслуживание: удалено данных чатов — %d", len(chat_ids))
        return len(chat_ids)

    async def run(self) -> tuple[int, int]:
        """Полный проход уборки: (очищено ответов, удалено чатов)."""
        answers = await self.purge_answer_texts()
        chats = await self.purge_inactive_chats()
        return answers, chats
