"""Сквозные проверки контента: общий кеш чатов, квота и банк вопросов."""

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.repositories import ChatRepo, UsageRepo
from bot.services.content import ContentService
from bot.services.fake_ai_client import FakeAIClient
from bot.services.group_lifecycle import register_bot_added
from scripts.seed_question_bank import load_bank

CHAT_ONE = -910
CHAT_TWO = -911


def _settings(**overrides: object) -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None, **overrides)


async def _chat(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    *,
    language: str = "ru",
    level: str = "B1",
):
    await register_bot_added(session_factory, chat_id=chat_id, title="G", chat_type="group")
    async with session_factory() as session:
        await ChatRepo(session).update_settings(chat_id, translation_language=language, level=level)
        await session.commit()
        chat = await ChatRepo(session).get(chat_id)
    assert chat is not None
    return chat


def _service(
    session_factory: async_sessionmaker[AsyncSession],
    client: FakeAIClient,
    settings: Settings | None = None,
) -> ContentService:
    return ContentService(session_factory, client, settings or _settings())


def _write_seed_file(directory: Path, count: int) -> None:
    items = [
        {
            "question": f"Seed question {i}?",
            "options": ["a", "b", "c", "d"],
            "correct_index": 0,
            "explanation": "x",
        }
        for i in range(count)
    ]
    (directory / "quiz_batch_B1.json").write_text(
        json.dumps(
            {
                "kind": "quiz_batch",
                "level": "B1",
                "language": "ru",
                "generated_at": "2026-10-23T00:00:00+00:00",
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def test_two_chats_share_daily_words(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Два чата одного уровня и языка получают слова из одного кеша."""
    first = await _chat(session_factory, CHAT_ONE)
    second = await _chat(session_factory, CHAT_TWO)
    client = FakeAIClient()
    service = _service(session_factory, client)

    result_one = await service.get_words(first)
    result_two = await service.get_words(second)

    assert len(result_one.items) == 10
    assert result_one.items == result_two.items
    assert len(client.calls_of("words")) == 1  # второй чат взял из кеша


async def test_two_chats_share_grammar(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await _chat(session_factory, CHAT_ONE)
    second = await _chat(session_factory, CHAT_TWO)
    client = FakeAIClient()
    service = _service(session_factory, client)

    result_one = await service.get_grammar(first)
    result_two = await service.get_grammar(second)

    assert result_one.items == result_two.items
    assert len(client.calls_of("grammar")) == 1


async def test_seed_bank_serves_quiz_when_quota_is_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Загруженный банк работает без ИИ, когда квота исчерпана."""
    _write_seed_file(tmp_path, 5)
    await load_bank(session_factory, tmp_path)

    await _chat(session_factory, CHAT_ONE)
    today = datetime.now(UTC).date()
    async with session_factory() as session:
        await ChatRepo(session).update_settings(CHAT_ONE, ai_quota_per_day=1)
        await UsageRepo(session).increment(CHAT_ONE, today)
        await session.commit()
        chat = await ChatRepo(session).get(CHAT_ONE)
    assert chat is not None

    client = FakeAIClient()
    service = _service(session_factory, client)

    result = await service.get_quiz(chat, 5)

    assert len(result.items) == 5
    assert client.calls_of("quiz_batch") == []  # ИИ не вызывался


async def test_quiz_batches_do_not_repeat_between_servings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Две порции квиза подряд не пересекаются и берутся без второго запроса."""
    chat = await _chat(session_factory, CHAT_ONE)
    client = FakeAIClient()
    service = _service(session_factory, client)

    first = await service.get_quiz(chat, 5)
    second = await service.get_quiz(chat, 5)

    first_titles = {item["question"] for item in first.items}
    second_titles = {item["question"] for item in second.items}
    assert len(first_titles) == 5
    assert len(second_titles) == 5
    assert not first_titles & second_titles
    assert len(client.calls_of("quiz_batch")) == 1
