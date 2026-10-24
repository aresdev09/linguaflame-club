"""Сервис контента: общий кеш, банк вопросов и генерация через ИИ.

Порядок добычи контента для групп задаёт скил `group-content-ai`:
сначала общий кеш (слова и грамматика дня), затем банк вопросов
(квиз и мини-игры), и только потом запрос к ИИ — с учётом квот.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import Chat
from bot.repositories import ContentRepo, UsageRepo
from bot.services.ai_client import AIClient, UsageInfo
from bot.services.ai_errors import AIError
from bot.services.ai_quality import content_hash, dedupe_payloads
from bot.services.ai_safety import find_stop_word_in

logger = logging.getLogger(__name__)

# История контента чата: повторы запрещены 30 дней.
RECENT_CONTENT_DAYS = 30
# Слова дня не повторяются на уровне 14 дней.
RECENT_WORDS_DAYS = 14

# Причины пустого результата: роутеры превращают их в локализованные тексты.
REASON_QUOTA = "quota"
REASON_AI = "ai_unavailable"
REASON_EMPTY = "empty"


@dataclass
class ContentResult:
    """Что удалось собрать для группы и почему, если пусто."""

    items: list[dict]
    reason: str | None = None


class ContentService:
    """Доступ к контенту: кеш → банк → ИИ, с квотами и историей чата."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ai_client: AIClient,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._ai_client = ai_client
        self._settings = settings
        self._last_reason: str | None = None

    # --- квиз и мини-игры (банк вопросов + генерация) ---

    async def get_quiz(self, chat: Chat, count: int) -> ContentResult:
        """Вопросы квиза: без повторов в чате за 30 дней."""
        return await self._bank_or_ai(chat, "quiz_batch", count)

    async def get_scramble(self, chat: Chat, count: int = 1) -> ContentResult:
        """Элементы «Собери слово»."""
        return await self._bank_or_ai(chat, "scramble", count)

    async def get_gap(self, chat: Chat, count: int = 1) -> ContentResult:
        """Элементы «Пропущенное слово»."""
        return await self._bank_or_ai(chat, "gap", count)

    async def get_race(self, chat: Chat, count: int = 5) -> ContentResult:
        """Элементы гонки переводов."""
        return await self._bank_or_ai(chat, "race", count)

    async def get_truefalse(self, chat: Chat, count: int = 5) -> ContentResult:
        """Элементы «Правда или ложь»."""
        return await self._bank_or_ai(chat, "truefalse", count)

    # --- слова и грамматика дня (общий кеш) ---

    async def get_words(self, chat: Chat) -> ContentResult:
        """Слова дня: одинаковые для групп одного уровня и языка в один день."""
        today = self._chat_today(chat)
        count = chat.words_count
        async with self._session_factory() as session:
            cached = await ContentRepo(session).get_cached(
                "words", chat.level, chat.translation_language, on_date=today
            )
        if cached is not None:
            words = list(cached.payload.get("words", []))
            if len(words) >= count:
                return ContentResult(words[:count])

        if not await self._ai_allowed(chat):
            return ContentResult([], REASON_QUOTA)
        exclude = await self._recent_words(chat)
        try:
            response = await self._ai_client.generate_words(
                chat.level,
                chat.translation_language,
                count=count,
                exclude_words=exclude,
            )
        except AIError as exc:
            logger.warning("ИИ не сгенерировал слова дня: %s", exc)
            return ContentResult([], REASON_AI)

        payloads = [item.model_dump() for item in response.words]
        payloads = [item for item in payloads if find_stop_word_in(item) is None]
        if len(payloads) != count:
            logger.warning("Слова дня не прошли проверку: %d из %d", len(payloads), count)
            return ContentResult([], REASON_AI)

        async with self._session_factory() as session:
            async with session.begin():
                await ContentRepo(session).put_cached(
                    "words",
                    chat.level,
                    chat.translation_language,
                    {"words": payloads},
                    on_date=today,
                )
        await self._record_usage(chat, self._ai_client.last_usage)
        return ContentResult(payloads)

    async def get_grammar(self, chat: Chat) -> ContentResult:
        """Урок грамматики дня: один на уровень и язык в один день."""
        today = self._chat_today(chat)
        async with self._session_factory() as session:
            cached = await ContentRepo(session).get_cached(
                "grammar", chat.level, chat.translation_language, on_date=today
            )
        if cached is not None:
            return ContentResult([cached.payload])

        if not await self._ai_allowed(chat):
            return ContentResult([], REASON_QUOTA)
        try:
            response = await self._ai_client.generate_grammar(
                chat.level, chat.translation_language
            )
        except AIError as exc:
            logger.warning("ИИ не сгенерировал грамматику дня: %s", exc)
            return ContentResult([], REASON_AI)

        payload = response.model_dump()
        if find_stop_word_in(payload) is not None:
            return ContentResult([], REASON_AI)

        async with self._session_factory() as session:
            async with session.begin():
                await ContentRepo(session).put_cached(
                    "grammar",
                    chat.level,
                    chat.translation_language,
                    payload,
                    on_date=today,
                )
        await self._record_usage(chat, self._ai_client.last_usage)
        return ContentResult([payload])

    # --- внутренняя механика ---

    async def _bank_or_ai(self, chat: Chat, kind: str, count: int) -> ContentResult:
        """Берёт элементы из банка, при нехватке — генерирует пачку через ИИ."""
        served = await self._serve_from_bank(chat, kind, count)
        if len(served) >= count:
            return ContentResult(served[:count])

        generated = await self._generate_and_store(chat, kind)
        fresh = generated[: count - len(served)]
        await self._mark_used(chat.id, kind, fresh)

        items = served + fresh
        if items:
            return ContentResult(items)
        return ContentResult([], self._last_reason)

    async def _serve_from_bank(self, chat: Chat, kind: str, count: int) -> list[dict]:
        """Свежие для чата элементы банка: без стоп-слов и повторов за 30 дней."""
        recent = await self._recent_hashes(chat)
        async with self._session_factory() as session:
            async with session.begin():
                repo = ContentRepo(session)
                candidates = await repo.pick_bank_items(
                    kind, chat.level, chat.translation_language, limit=count * 4
                )
                served: list[dict] = []
                for item in candidates:
                    payload = item.payload
                    if find_stop_word_in(payload) is not None:
                        continue
                    digest = content_hash(kind, payload)
                    if digest in recent:
                        continue
                    recent.add(digest)  # не выдаём один и тот же дважды за вызов
                    served.append(payload)
                    await repo.mark_content_used(chat.id, digest, kind)
                    if len(served) == count:
                        break
        return served

    async def _generate_and_store(self, chat: Chat, kind: str) -> list[dict]:
        """Генерирует пачку через ИИ, фильтрует и кладёт в банк вопросов."""
        self._last_reason = None
        if not await self._ai_allowed(chat):
            self._last_reason = REASON_QUOTA
            return []
        try:
            payloads = await self._ai_payloads(chat, kind)
        except AIError as exc:
            logger.warning("ИИ недоступен (%s): %s", kind, exc)
            self._last_reason = REASON_AI
            return []

        recent = await self._recent_hashes(chat)
        payloads = [item for item in payloads if find_stop_word_in(item) is None]
        payloads = dedupe_payloads(kind, payloads)
        payloads = [
            item for item in payloads if content_hash(kind, item) not in recent
        ]
        if not payloads:
            self._last_reason = REASON_EMPTY
            return []

        async with self._session_factory() as session:
            async with session.begin():
                repo = ContentRepo(session)
                for payload in payloads:
                    await repo.add_bank_item(
                        kind, chat.level, chat.translation_language, payload
                    )
        await self._record_usage(chat, self._ai_client.last_usage)
        return payloads

    async def _ai_payloads(self, chat: Chat, kind: str) -> list[dict]:
        """Запрашивает у ИИ пачку контента нужного вида."""
        client = self._ai_client
        level, language = chat.level, chat.translation_language
        if kind == "quiz_batch":
            response = await client.generate_quiz_batch(level, language)
            return [item.model_dump() for item in response.questions]
        if kind == "scramble":
            response = await client.generate_scramble(level, language)
            return [item.model_dump() for item in response.items]
        if kind == "gap":
            response = await client.generate_gap(level, language)
            return [item.model_dump() for item in response.items]
        if kind == "race":
            response = await client.generate_race(level, language)
            return [item.model_dump() for item in response.items]
        if kind == "truefalse":
            response = await client.generate_truefalse(level, language)
            return [item.model_dump() for item in response.items]
        raise ValueError(f"неизвестный вид контента: {kind}")

    async def _mark_used(self, chat_id: int, kind: str, payloads: list[dict]) -> None:
        """Отмечает в истории чата контент, который будет показан."""
        if not payloads:
            return
        async with self._session_factory() as session:
            async with session.begin():
                repo = ContentRepo(session)
                for payload in payloads:
                    await repo.mark_content_used(
                        chat_id, content_hash(kind, payload), kind
                    )

    async def _ai_allowed(self, chat: Chat) -> bool:
        """Проверяет квоту чата и глобальный дневной бюджет запросов."""
        today = self._chat_today(chat)
        utc_today = datetime.now(UTC).date()
        async with self._session_factory() as session:
            usage = UsageRepo(session)
            if chat.ai_quota_per_day > 0:
                used = await usage.get_requests(chat.id, today)
                if used >= chat.ai_quota_per_day:
                    return False
            if self._settings.ai_global_daily_limit > 0:
                total = await usage.total_requests(utc_today)
                if total >= self._settings.ai_global_daily_limit:
                    return False
        return True

    async def _record_usage(self, chat: Chat, usage: UsageInfo | None) -> None:
        """Записывает запрос к ИИ (и токены) в дневную статистику чата."""
        tokens = usage.total_tokens if usage is not None else 0
        today = self._chat_today(chat)
        async with self._session_factory() as session:
            async with session.begin():
                await UsageRepo(session).increment(chat.id, today, tokens=tokens)

    async def _recent_words(self, chat: Chat) -> set[str]:
        """Слова прошлых двух недель уровня и языка — их просим не повторять."""
        since = self._chat_today(chat) - timedelta(days=RECENT_WORDS_DAYS)
        async with self._session_factory() as session:
            return await ContentRepo(session).recent_words(
                chat.level, chat.translation_language, since=since
            )

    async def _recent_hashes(self, chat: Chat) -> set[str]:
        """Хеши контента, выданного чату за последние 30 дней."""
        since = datetime.now(UTC) - timedelta(days=RECENT_CONTENT_DAYS)
        async with self._session_factory() as session:
            return await ContentRepo(session).recent_hashes(chat.id, since=since)

    @staticmethod
    def _chat_today(chat: Chat) -> date:
        """Сегодняшняя дата по часовому поясу чата."""
        try:
            timezone = ZoneInfo(chat.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            timezone = UTC
        return datetime.now(timezone).date()
