"""Тесты сервиса контента: кеш, банк, квоты, история и фильтры."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.models import AIUsage
from bot.repositories import ChatRepo, ContentRepo, UsageRepo
from bot.services.ai_client import UsageInfo
from bot.services.ai_errors import AIRequestError
from bot.services.ai_schemas import (
    GrammarLesson,
    QuizBatch,
    QuizQuestion,
    WordItem,
    WordsResponse,
)
from bot.services.content import (
    REASON_AI,
    REASON_EMPTY,
    REASON_QUOTA,
    ContentService,
)
from bot.services.group_lifecycle import register_bot_added

CHAT_ID = -900
OTHER_CHAT_ID = -901


def _settings(**overrides: object) -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None, **overrides)


def _quiz_batch(count: int, *, start: int = 1) -> QuizBatch:
    return QuizBatch(
        questions=[
            QuizQuestion(
                question=f"Question {i}: pick the right option.",
                options=[f"opt {i}a", f"opt {i}b", f"opt {i}c", f"opt {i}d"],
                correct_index=i % 4,
                explanation=f"explanation {i}",
            )
            for i in range(start, start + count)
        ]
    )


def _quiz_batch_with_unsafe() -> QuizBatch:
    questions = list(_quiz_batch(2).questions)
    questions.append(
        QuizQuestion(
            question="Which word about the war is correct?",
            options=["army", "navy", "peace", "tank"],
            correct_index=2,
            explanation="peace — мир",
        )
    )
    return QuizBatch(questions=questions)


def _word(index: int) -> dict:
    return {
        "word": f"word{index}",
        "transcription": f"/w{index}/",
        "translation": f"перевод {index}",
        "example": f"Example sentence {index}.",
    }


def _words(count: int) -> WordsResponse:
    return WordsResponse(words=[WordItem(**_word(i)) for i in range(count)])


def _grammar_lesson() -> GrammarLesson:
    return GrammarLesson(
        topic="Present Simple",
        explanation="Регулярные действия в настоящем.",
        examples=["I read every day.", "She works here."],
        exercise={
            "question": "He ___ books.",
            "options": ["read", "reads"],
            "correct_index": 1,
        },
        answer_explanation="С he используется reads.",
    )


class StubAIClient:
    """Минимальная заглушка клиента: заданные ответы и запись вызовов."""

    def __init__(
        self,
        *,
        quiz_batch: QuizBatch | None = None,
        words: WordsResponse | None = None,
        grammar: GrammarLesson | None = None,
        error: Exception | None = None,
        usage: UsageInfo | None = None,
    ) -> None:
        self.quiz_batch = quiz_batch
        self.words = words
        self.grammar = grammar
        self.error = error
        self.last_usage = usage or UsageInfo(prompt_tokens=10, completion_tokens=20)
        self.calls: list[dict] = []
        self._quiz_calls = 0

    async def close(self) -> None:
        return None

    async def generate_quiz_batch(
        self, level: str, language: str, *, count: int = 20
    ) -> QuizBatch:
        self.calls.append({"task": "quiz_batch", "level": level, "language": language})
        self._raise()
        self._quiz_calls += 1
        if self.quiz_batch is not None:
            return self.quiz_batch
        return _quiz_batch(10, start=self._quiz_calls * 100)

    async def generate_words(
        self,
        level: str,
        language: str,
        *,
        count: int,
        exclude_words: object = (),
    ) -> WordsResponse:
        self.calls.append(
            {"task": "words", "count": count, "exclude_words": list(exclude_words)}
        )
        self._raise()
        if self.words is not None:
            return self.words
        return _words(count)

    async def generate_grammar(self, level: str, language: str) -> GrammarLesson:
        self.calls.append({"task": "grammar", "level": level, "language": language})
        self._raise()
        return self.grammar or _grammar_lesson()

    def calls_of(self, task: str) -> list[dict]:
        return [call for call in self.calls if call["task"] == task]

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error


async def _chat(session_factory: async_sessionmaker[AsyncSession], chat_id: int = CHAT_ID):
    await register_bot_added(
        session_factory, chat_id=chat_id, title="G", chat_type="group"
    )
    async with session_factory() as session:
        chat = await ChatRepo(session).get(chat_id)
    assert chat is not None
    return chat


def _service(
    session_factory: async_sessionmaker[AsyncSession],
    stub: StubAIClient,
    settings: Settings | None = None,
) -> ContentService:
    return ContentService(session_factory, stub, settings or _settings())


async def test_quiz_returns_requested_count_and_stores_batch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)

    result = await service.get_quiz(chat, 5)

    assert result.reason is None
    assert len(result.items) == 5
    assert len(stub.calls_of("quiz_batch")) == 1
    async with session_factory() as session:
        stored = await ContentRepo(session).pick_bank_items("quiz_batch", "B1", "en", 100)
    assert len(stored) == 10


async def test_quiz_does_not_repeat_recent_items(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)

    first = await service.get_quiz(chat, 5)
    second = await service.get_quiz(chat, 5)

    questions_first = {item["question"] for item in first.items}
    questions_second = {item["question"] for item in second.items}
    assert len(questions_second) == 5
    assert not questions_first & questions_second
    assert len(stub.calls_of("quiz_batch")) == 1


async def test_quiz_generates_new_batch_when_bank_is_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)

    await service.get_quiz(chat, 5)
    await service.get_quiz(chat, 5)
    third = await service.get_quiz(chat, 5)

    assert third.reason is None
    assert len(third.items) == 5
    assert len(stub.calls_of("quiz_batch")) == 2


async def test_repeated_ai_content_is_dropped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ИИ вернул те же вопросы — свежего контента нет."""
    chat = await _chat(session_factory)
    stub = StubAIClient(quiz_batch=_quiz_batch(5))
    service = _service(session_factory, stub)

    await service.get_quiz(chat, 5)
    second = await service.get_quiz(chat, 5)

    assert second.items == []
    assert second.reason == REASON_EMPTY


async def test_quota_exhausted_uses_bank(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)

    await service.get_quiz(chat, 5)  # наполнили банк, квота израсходована
    async with session_factory() as session:
        await ChatRepo(session).update_settings(CHAT_ID, ai_quota_per_day=1)
        await session.commit()
        chat = await ChatRepo(session).get(CHAT_ID)

    result = await service.get_quiz(chat, 5)

    assert result.reason is None
    assert len(result.items) == 5
    assert len(stub.calls_of("quiz_batch")) == 1  # из банка, без ИИ


async def test_quota_exhausted_without_bank_returns_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)
    today = datetime.now(UTC).date()

    async with session_factory() as session:
        await ChatRepo(session).update_settings(CHAT_ID, ai_quota_per_day=1)
        await UsageRepo(session).increment(CHAT_ID, today)
        await session.commit()
        chat = await ChatRepo(session).get(CHAT_ID)

    result = await service.get_quiz(chat, 5)

    assert result.items == []
    assert result.reason == REASON_QUOTA
    assert stub.calls_of("quiz_batch") == []


async def test_global_budget_blocks_ai(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    await _chat(session_factory, OTHER_CHAT_ID)
    stub = StubAIClient()
    service = _service(session_factory, stub, _settings(ai_global_daily_limit=1))
    today = datetime.now(UTC).date()

    async with session_factory() as session:
        await UsageRepo(session).increment(OTHER_CHAT_ID, today)
        await session.commit()

    result = await service.get_quiz(chat, 5)

    assert result.items == []
    assert result.reason == REASON_QUOTA
    assert stub.calls_of("quiz_batch") == []


async def test_ai_error_returns_reason(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient(error=AIRequestError("down"))
    service = _service(session_factory, stub)

    result = await service.get_quiz(chat, 5)

    assert result.items == []
    assert result.reason == REASON_AI


async def test_unsafe_items_are_dropped_before_storing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient(quiz_batch=_quiz_batch_with_unsafe())
    service = _service(session_factory, stub)

    result = await service.get_quiz(chat, 2)

    assert len(result.items) == 2
    for item in result.items:
        assert "war" not in item["question"].lower()
    async with session_factory() as session:
        stored = await ContentRepo(session).pick_bank_items("quiz_batch", "B1", "en", 100)
    assert len(stored) == 2  # вопрос про войну отброшен


async def test_usage_is_recorded_in_ai_usage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)
    today = datetime.now(UTC).date()

    await service.get_quiz(chat, 5)

    async with session_factory() as session:
        requests = await UsageRepo(session).get_requests(CHAT_ID, today)
        usage = await session.get(AIUsage, (CHAT_ID, today))
    assert requests == 1
    assert usage is not None
    assert usage.tokens == 30


async def test_words_are_cached_for_all_chats(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)
    today = datetime.now(UTC).date()

    first = await service.get_words(chat)
    second = await service.get_words(chat)

    assert len(first.items) == 10
    assert first.items == second.items
    assert len(stub.calls_of("words")) == 1
    async with session_factory() as session:
        cached = await ContentRepo(session).get_cached("words", "B1", "en", on_date=today)
    assert cached is not None


async def test_words_exclude_recent_level_words(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)
    yesterday = datetime.now(UTC).date() - timedelta(days=1)

    async with session_factory() as session:
        async with session.begin():
            await ContentRepo(session).put_cached(
                "words", "B1", "en", {"words": [_word(0)]}, on_date=yesterday
            )

    await service.get_words(chat)

    excluded = stub.calls_of("words")[0]["exclude_words"]
    assert "word0" in excluded


async def test_words_count_mismatch_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient(words=_words(3))  # просили 10
    service = _service(session_factory, stub)

    result = await service.get_words(chat)

    assert result.items == []
    assert result.reason == REASON_AI


async def test_grammar_is_cached(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _chat(session_factory)
    stub = StubAIClient()
    service = _service(session_factory, stub)

    first = await service.get_grammar(chat)
    second = await service.get_grammar(chat)

    assert first.items == second.items
    assert first.items[0]["topic"] == "Present Simple"
    assert len(stub.calls_of("grammar")) == 1
