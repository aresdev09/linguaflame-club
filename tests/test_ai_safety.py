"""Тесты фильтра стоп-слов и требований безопасности в промпте."""

from bot.prompts.base import build_system_prompt
from bot.services.ai_safety import find_stop_word, find_stop_word_in, is_safe


def test_finds_stop_word_in_sentence() -> None:
    assert find_stop_word("The war started in spring.") == "war"


def test_clean_text_is_safe() -> None:
    # «war» внутри «warm» не считается: сравниваются целые слова.
    assert find_stop_word("I like warm soup.") is None
    assert is_safe("She reads a book every evening.")


def test_finds_stop_word_inside_payload() -> None:
    payload = {
        "statement": "We talk about politics.",
        "options": ["a clean option", "another one"],
        "nested": {"explanation": "totally clean"},
    }
    assert find_stop_word_in(payload) == "politics"


def test_payload_with_clean_text_passes() -> None:
    payload = {"question": "Choose the correct word.", "options": ["go", "goes"]}
    assert find_stop_word_in(payload) is None


def test_system_prompt_requires_safe_content() -> None:
    prompt = build_system_prompt("B1", "ru")
    assert "safe for all ages" in prompt
    assert "violence" in prompt
    assert "politics" in prompt
