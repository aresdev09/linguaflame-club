"""Тесты заглушки клиента ИИ."""

import pytest

from bot.services.ai_client import AIClient
from bot.services.ai_errors import AIResponseError
from bot.services.fake_ai_client import FakeAIClient, default_quiz_batch

GENERATORS = (
    "generate_quiz_batch",
    "generate_words",
    "generate_grammar",
    "generate_scramble",
    "generate_gap",
    "generate_race",
    "generate_truefalse",
)


def test_fake_mirrors_ai_client_interface() -> None:
    for name in GENERATORS:
        assert hasattr(AIClient, name)
        assert hasattr(FakeAIClient, name)


async def test_default_batches_match_requested_counts() -> None:
    client = FakeAIClient()

    quiz = await client.generate_quiz_batch("B1", "ru", count=7)
    assert len(quiz.questions) == 7

    words = await client.generate_words("B1", "ru", count=5)
    assert len(words.words) == 5

    gap = await client.generate_gap("B1", "ru", count=3)
    assert len(gap.items) == 3

    scramble = await client.generate_scramble("B1", "ru", count=2)
    assert len(scramble.items) == 2

    truefalse = await client.generate_truefalse("B1", "ru", count=4)
    assert len(truefalse.items) == 4


async def test_fake_records_calls() -> None:
    client = FakeAIClient()

    await client.generate_words("B1", "ru", count=5, exclude_words={"apple"})

    calls = client.calls_of("words")
    assert len(calls) == 1
    assert calls[0]["count"] == 5
    assert calls[0]["exclude_words"] == ["apple"]


async def test_fake_can_raise_error() -> None:
    client = FakeAIClient(error=AIResponseError("bad answer"))
    with pytest.raises(AIResponseError):
        await client.generate_quiz_batch("B1", "ru")


async def test_fake_returns_configured_response() -> None:
    batch = default_quiz_batch(2, start=50)
    client = FakeAIClient(quiz=batch)

    result = await client.generate_quiz_batch("B1", "ru")

    assert result is batch


async def test_fake_quiz_batches_do_not_repeat_by_default() -> None:
    """Каждая пачка отличается: удобно для проверок «без повторов»."""
    client = FakeAIClient()

    first = await client.generate_quiz_batch("B1", "ru", count=3)
    second = await client.generate_quiz_batch("B1", "ru", count=3)

    first_keys = {q.question for q in first.questions}
    second_keys = {q.question for q in second.questions}
    assert not first_keys & second_keys
