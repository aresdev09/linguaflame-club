"""Тесты схем ИИ: структура, лимиты длины, единственный верный вариант."""

import pytest
from pydantic import ValidationError

from bot.services.ai_schemas import (
    GapItem,
    GrammarExercise,
    QuizQuestion,
    ScrambleItem,
    WordsResponse,
)


def _quiz(**overrides: object) -> dict:
    data: dict = {
        "question": "She ___ to school every day.",
        "options": ["go", "goes", "going", "gone"],
        "correct_index": 1,
        "explanation": "С he используется goes.",
    }
    data.update(overrides)
    return data


def test_valid_quiz_question_parses() -> None:
    question = QuizQuestion.model_validate(_quiz())
    assert question.correct_index == 1


def test_quiz_requires_four_options() -> None:
    with pytest.raises(ValidationError):
        QuizQuestion.model_validate(_quiz(options=["go", "goes", "going"]))


def test_quiz_rejects_duplicate_options() -> None:
    with pytest.raises(ValidationError):
        QuizQuestion.model_validate(_quiz(options=["go", "go", "going", "gone"]))


def test_quiz_rejects_out_of_range_correct_index() -> None:
    with pytest.raises(ValidationError):
        QuizQuestion.model_validate(_quiz(correct_index=4))


def test_quiz_rejects_long_question_and_option() -> None:
    with pytest.raises(ValidationError):
        QuizQuestion.model_validate(_quiz(question="?" * 301))
    with pytest.raises(ValidationError):
        QuizQuestion.model_validate(_quiz(options=["a" * 61, "b", "c", "d"]))


def test_gap_requires_exactly_one_blank() -> None:
    valid = {
        "sentence": "She ___ home.",
        "options": ["a", "b", "c", "d"],
        "correct_index": 0,
        "explanation": "x",
    }
    assert GapItem.model_validate(valid).sentence == "She ___ home."
    with pytest.raises(ValidationError):
        GapItem.model_validate({**valid, "sentence": "She ___ ___ home."})
    with pytest.raises(ValidationError):
        GapItem.model_validate({**valid, "sentence": "She home."})


def test_scramble_word_rules() -> None:
    item = ScrambleItem.model_validate({"word": "Beautiful", "hint": "красивый"})
    assert item.word == "beautiful"
    for bad in ("cat", "beautifully", "ice-cream", "two words", "word123"):
        with pytest.raises(ValidationError):
            ScrambleItem.model_validate({"word": bad, "hint": "x"})


def test_grammar_exercise_option_bounds() -> None:
    exercise = GrammarExercise.model_validate(
        {"question": "Q", "options": ["a", "b"], "correct_index": 1}
    )
    assert exercise.correct_index == 1
    with pytest.raises(ValidationError):
        GrammarExercise.model_validate({"question": "Q", "options": ["a"], "correct_index": 0})
    with pytest.raises(ValidationError):
        GrammarExercise.model_validate(
            {"question": "Q", "options": ["a", "a", "b", "c"], "correct_index": 0}
        )


def test_words_require_non_empty_list() -> None:
    with pytest.raises(ValidationError):
        WordsResponse.model_validate({"words": []})
