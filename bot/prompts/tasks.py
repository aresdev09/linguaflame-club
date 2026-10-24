"""Промпты задач генерации контента.

Каждый промпт описывает ровно тот JSON, который проверяет схема из
`bot.services.ai_schemas` — форматы из скила `group-content-ai`.
"""

from collections.abc import Iterable

# Сколько вопросов квиза просим за один запрос (хранится в question_bank).
QUIZ_BATCH_SIZE = 20
# Сколько элементов мини-игр просим за один запрос.
GAME_BATCH_SIZE = 10


def quiz_batch_prompt(count: int) -> str:
    return (
        f"Create {count} English quiz questions for this level.\n"
        'Return JSON: {"questions": [{"question": "...", '
        '"options": ["...", "...", "...", "..."], "correct_index": 0, '
        '"explanation": "..."}]}\n'
        "Rules: exactly 4 different options per question and exactly one correct option "
        "(correct_index is its index); question up to 300 characters; each option up to "
        "60 characters; explanation in the translation language; no duplicate questions."
    )


def words_prompt(count: int, exclude_words: Iterable[str] = ()) -> str:
    lines = [
        f"Create {count} English words for the daily vocabulary post.",
        'Return JSON: {"words": [{"word": "...", "transcription": "/.../", '
        '"translation": "...", "example": "..."}]}',
        f"Rules: exactly {count} unique words; a short example sentence for each word; "
        "translation and transcription in the translation language.",
    ]
    excluded = sorted(exclude_words)
    if excluded:
        lines.append("Do not use these words: " + ", ".join(excluded) + ".")
    return "\n".join(lines)


def grammar_prompt() -> str:
    return (
        "Create one short grammar lesson for this level.\n"
        'Return JSON: {"topic": "Present Perfect", "explanation": "...", '
        '"examples": ["...", "..."], "exercise": {"question": "...", '
        '"options": ["...", "..."], "correct_index": 1}, "answer_explanation": "..."}\n'
        "Rules: explanation, examples and answer_explanation in the translation language; "
        "2-3 examples; the exercise has 2-4 different options and exactly one correct option."
    )


def scramble_prompt(count: int) -> str:
    return (
        f"Create {count} English words for a word scramble game.\n"
        'Return JSON: {"items": [{"word": "beautiful", "hint": "красивый"}]}\n'
        "Rules: each word is 5 to 10 letters, letters only (no spaces, digits or hyphens); "
        "hint is the translation in the translation language."
    )


def gap_prompt(count: int) -> str:
    return (
        f"Create {count} fill-the-gap sentences for this level.\n"
        'Return JSON: {"items": [{"sentence": "She ___ to school every day.", '
        '"options": ["...", "...", "...", "..."], "correct_index": 0, '
        '"explanation": "..."}]}\n'
        "Rules: exactly one gap written as ___ in each sentence; exactly 4 different "
        "options and one correct option; explanation in the translation language."
    )


def race_prompt(count: int) -> str:
    return (
        f"Create {count} translation race items for this level.\n"
        'Return JSON: {"items": [{"word": "journey", "options": ["...", "...", "...", "..."], '
        '"correct_index": 0}]}\n'
        "Rules: an English word and exactly 4 different translations as options in the "
        "translation language; exactly one correct option."
    )


def truefalse_prompt(count: int) -> str:
    return (
        f"Create {count} true-or-false statements about English grammar or vocabulary.\n"
        'Return JSON: {"items": [{"statement": "He go to school is correct.", '
        '"answer": false, "explanation": "..."}]}\n'
        "Rules: answer is true or false; explanation in the translation language; "
        "mix true and false statements."
    )
