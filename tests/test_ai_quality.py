"""Тесты проверок качества контента: слова, дедупликация, хеш."""

import pytest

from bot.services.ai_quality import (
    content_hash,
    dedupe_payloads,
    item_key,
    require_word_count,
)
from bot.services.ai_schemas import WordItem, WordsResponse


def _word(name: str) -> dict:
    return {
        "word": name,
        "transcription": f"/{name}/",
        "translation": name,
        "example": f"I like {name}.",
    }


def test_require_word_count_accepts_exact_count() -> None:
    response = WordsResponse(words=[WordItem(**_word(w)) for w in ("apple", "book")])
    require_word_count(2)(response)


def test_require_word_count_rejects_wrong_count() -> None:
    response = WordsResponse(words=[WordItem(**_word("apple"))])
    with pytest.raises(ValueError):
        require_word_count(2)(response)


def test_require_word_count_rejects_duplicates() -> None:
    response = WordsResponse(words=[WordItem(**_word("apple")), WordItem(**_word("Apple"))])
    with pytest.raises(ValueError):
        require_word_count(2)(response)


def test_dedupe_payloads_keeps_first_and_order() -> None:
    payloads = [
        {"question": "What is  it?"},
        {"question": "what is it?"},
        {"question": "Another question?"},
    ]
    assert dedupe_payloads("quiz_batch", payloads) == [payloads[0], payloads[2]]


def test_item_key_normalizes_whitespace_and_case() -> None:
    assert item_key("scramble", {"word": "  Apple "}) == "apple"
    assert item_key("unknown_kind", {"word": "Apple"}) == ""


def test_content_hash_is_stable_but_order_independent() -> None:
    first = {"options": ["a", "b"], "question": "Q"}
    second = {"question": "Q", "options": ["a", "b"]}
    assert content_hash("quiz_batch", first) == content_hash("quiz_batch", second)
    assert content_hash("quiz_batch", first) != content_hash("gap", first)
