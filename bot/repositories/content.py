"""Репозиторий контента: общий кеш, банк вопросов, история и посты дня."""

from datetime import date, datetime

from sqlalchemy import select

from bot.models import (
    ChatContentHistory,
    ContentCache,
    DailyPost,
    QuestionBankItem,
    utcnow,
)
from bot.repositories.base import BaseRepo


class ContentRepo(BaseRepo):
    """Доступ к таблицам контента и дневных постов."""

    # --- Общий кеш контента (переиспользуется между группами) ---

    async def get_cached(
        self,
        kind: str,
        level: str,
        language: str,
        *,
        on_date: date | None = None,
    ) -> ContentCache | None:
        """Готовый контент для уровня, языка и даты, если он уже есть."""
        stmt = select(ContentCache).where(
            ContentCache.kind == kind,
            ContentCache.level == level,
            ContentCache.language == language,
        )
        if on_date is None:
            stmt = stmt.where(ContentCache.date.is_(None))
        else:
            stmt = stmt.where(ContentCache.date == on_date)
        stmt = stmt.order_by(ContentCache.created_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def put_cached(
        self,
        kind: str,
        level: str,
        language: str,
        payload: dict,
        *,
        on_date: date | None = None,
    ) -> ContentCache:
        """Кладёт сгенерированный контент в общий кеш."""
        cached = ContentCache(
            kind=kind,
            level=level,
            language=language,
            payload=payload,
            date=on_date,
        )
        self.session.add(cached)
        await self.session.flush()
        return cached

    # --- Банк вопросов (запасной контент без ИИ) ---

    async def add_bank_item(
        self, kind: str, level: str, language: str, payload: dict
    ) -> QuestionBankItem:
        """Добавляет вопрос в банк."""
        item = QuestionBankItem(kind=kind, level=level, language=language, payload=payload)
        self.session.add(item)
        await self.session.flush()
        return item

    async def pick_bank_items(
        self,
        kind: str,
        level: str,
        language: str,
        limit: int,
    ) -> list[QuestionBankItem]:
        """Берёт вопросы из банка, начиная с реже всего использованных."""
        stmt = (
            select(QuestionBankItem)
            .where(
                QuestionBankItem.kind == kind,
                QuestionBankItem.level == level,
                QuestionBankItem.language == language,
            )
            .order_by(QuestionBankItem.times_used.asc(), QuestionBankItem.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_bank_item_used(self, item_id: int) -> None:
        """Отмечает, что вопрос из банка выдан группе."""
        item = await self.session.get(QuestionBankItem, item_id)
        if item is None:
            return
        item.times_used += 1
        await self.session.flush()

    # --- История контента чата (без повторов за 30 дней) ---

    async def was_content_used(
        self,
        chat_id: int,
        content_hash: str,
        *,
        since: datetime | None = None,
    ) -> bool:
        """True, если такой контент уже выдавался этому чату."""
        stmt = select(ChatContentHistory.content_hash).where(
            ChatContentHistory.chat_id == chat_id,
            ChatContentHistory.content_hash == content_hash,
        )
        if since is not None:
            stmt = stmt.where(ChatContentHistory.used_at >= since)
        result = await self.session.execute(stmt)
        return result.scalars().first() is not None

    async def mark_content_used(self, chat_id: int, content_hash: str, kind: str) -> None:
        """Запоминает контент, выданный чату."""
        history = await self.session.get(ChatContentHistory, (chat_id, content_hash))
        if history is None:
            history = ChatContentHistory(
                chat_id=chat_id, content_hash=content_hash, kind=kind
            )
            self.session.add(history)
        else:
            history.used_at = utcnow()
        await self.session.flush()

    async def recent_hashes(self, chat_id: int, *, since: datetime) -> set[str]:
        """Хеши контента, выданного чату с указанного времени."""
        stmt = select(ChatContentHistory.content_hash).where(
            ChatContentHistory.chat_id == chat_id,
            ChatContentHistory.used_at >= since,
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

    async def recent_words(self, level: str, language: str, *, since: date) -> set[str]:
        """Слова дня уровня и языка за последние дни — для исключений."""
        stmt = select(ContentCache.payload).where(
            ContentCache.kind == "words",
            ContentCache.level == level,
            ContentCache.language == language,
            ContentCache.date >= since,
        )
        result = await self.session.execute(stmt)
        words: set[str] = set()
        for payload in result.scalars().all():
            for item in (payload or {}).get("words", []):
                word = str(item.get("word", "")).strip().lower()
                if word:
                    words.add(word)
        return words

    # --- Посты дня ---

    async def get_daily_post(
        self, chat_id: int, on_date: date, kind: str
    ) -> DailyPost | None:
        stmt = select(DailyPost).where(
            DailyPost.chat_id == chat_id,
            DailyPost.date == on_date,
            DailyPost.kind == kind,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create_daily_post(
        self,
        chat_id: int,
        on_date: date,
        kind: str,
        *,
        payload: dict | None = None,
    ) -> DailyPost:
        """Создаёт запись до отправки: `posted_at` пустой — пост ещё не вышел.

        Уникальный индекс (chat_id, date, kind) не даст отправить дубль
        при повторном запуске задачи планировщика.
        """
        post = DailyPost(chat_id=chat_id, date=on_date, kind=kind, payload=payload)
        self.session.add(post)
        await self.session.flush()
        return post

    async def mark_daily_post_sent(self, post_id: int, message_id: int) -> None:
        """Отмечает пост отправленным."""
        post = await self.session.get(DailyPost, post_id)
        if post is None:
            return
        post.message_id = message_id
        post.posted_at = utcnow()
        await self.session.flush()
