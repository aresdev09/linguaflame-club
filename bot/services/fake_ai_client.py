"""Заглушка клиента ИИ для тестов и локальной разработки.

Методы повторяют `AIClient`: сетевых запросов нет, ответы задаются
заранее или генерируются детерминированно, каждый вызов пишется в
`calls`. Ошибку можно включить через `error` — как у живого клиента.
"""

from collections.abc import Iterable
from typing import Any

from bot.prompts import tasks
from bot.services.ai_client import UsageInfo
from bot.services.ai_schemas import (
    GapItem,
    GapResponse,
    GrammarLesson,
    QuizBatch,
    QuizQuestion,
    RaceItem,
    RaceResponse,
    ScrambleItem,
    ScrambleResponse,
    TrueFalseItem,
    TrueFalseResponse,
    WordItem,
    WordsResponse,
)

DEFAULT_USAGE = UsageInfo(prompt_tokens=100, completion_tokens=200)

# Базовые слова: подходят и для слов дня, и для «Собери слово» (5–10 букв).
_BASE_WORDS = (
    "apple",
    "letter",
    "morning",
    "journey",
    "kitchen",
    "orange",
    "people",
    "question",
    "river",
    "window",
    "garden",
    "family",
    "city",
    "book",
    "school",
    "evening",
    "night",
    "home",
    "idea",
    "day",
)


def default_quiz_batch(count: int, *, start: int = 1) -> QuizBatch:
    """Пачка вопросов квиза, которая проходит схему как есть."""
    return QuizBatch(
        questions=[
            QuizQuestion(
                question=f"Question {i}: choose the correct option.",
                options=[f"option {i}a", f"option {i}b", f"option {i}c", f"option {i}d"],
                correct_index=i % 4,
                explanation=f"Explanation {i}.",
            )
            for i in range(start, start + count)
        ]
    )


def default_words(count: int) -> WordsResponse:
    """Набор слов ровно по запросу."""
    words = []
    for index in range(count):
        word = _BASE_WORDS[index % len(_BASE_WORDS)]
        if index >= len(_BASE_WORDS):
            word = f"word{index}"
        words.append(
            WordItem(
                word=word,
                transcription=f"/{word}/",
                translation=f"translation of {word}",
                example=f"I know the word {word}.",
            )
        )
    return WordsResponse(words=words)


def default_grammar_lesson() -> GrammarLesson:
    return GrammarLesson(
        topic="Present Simple",
        explanation="Regular actions in the present.",
        examples=["I read every day.", "She works here."],
        exercise={
            "question": "He ___ books.",
            "options": ["read", "reads"],
            "correct_index": 1,
        },
        answer_explanation="With he we use reads.",
    )


def default_scramble_items(count: int) -> ScrambleResponse:
    return ScrambleResponse(
        items=[
            ScrambleItem(word=_BASE_WORDS[i % 10], hint=f"translation of {_BASE_WORDS[i % 10]}")
            for i in range(count)
        ]
    )


def default_gap_items(count: int) -> GapResponse:
    return GapResponse(
        items=[
            GapItem(
                sentence=f"Task {i}: she ___ to school every day.",
                options=[f"gap {i}a", f"gap {i}b", f"gap {i}c", f"gap {i}d"],
                correct_index=i % 4,
                explanation=f"Explanation {i}.",
            )
            for i in range(count)
        ]
    )


def default_race_items(count: int) -> RaceResponse:
    return RaceResponse(
        items=[
            RaceItem(
                word=f"word{i}",
                options=[f"race {i}a", f"race {i}b", f"race {i}c", f"race {i}d"],
                correct_index=i % 4,
            )
            for i in range(count)
        ]
    )


def default_truefalse_items(count: int) -> TrueFalseResponse:
    return TrueFalseResponse(
        items=[
            TrueFalseItem(
                statement=f"Statement number {i} is correct.",
                answer=i % 2 == 0,
                explanation=f"Explanation {i}.",
            )
            for i in range(count)
        ]
    )


class FakeAIClient:
    """Отдаёт заданные ответы и записывает вызовы; ошибку можно включить."""

    def __init__(
        self,
        *,
        quiz: QuizBatch | None = None,
        words: WordsResponse | None = None,
        grammar: GrammarLesson | None = None,
        scramble: ScrambleResponse | None = None,
        gap: GapResponse | None = None,
        race: RaceResponse | None = None,
        truefalse: TrueFalseResponse | None = None,
        error: Exception | None = None,
        usage: UsageInfo | None = None,
    ) -> None:
        self.quiz = quiz
        self.words = words
        self.grammar = grammar
        self.scramble = scramble
        self.gap = gap
        self.race = race
        self.truefalse = truefalse
        self.error = error
        self.last_usage = usage or DEFAULT_USAGE
        self.calls: list[dict[str, Any]] = []
        self._quiz_calls = 0

    async def close(self) -> None:
        """Совместимость с AIClient."""

    async def generate_quiz_batch(
        self, level: str, language: str, *, count: int = tasks.QUIZ_BATCH_SIZE
    ) -> QuizBatch:
        self._record("quiz_batch", level=level, language=language, count=count)
        if self.quiz is not None:
            return self._result(self.quiz)
        self._quiz_calls += 1
        return self._result(default_quiz_batch(count, start=self._quiz_calls * 100))

    async def generate_words(
        self,
        level: str,
        language: str,
        *,
        count: int,
        exclude_words: Iterable[str] = (),
    ) -> WordsResponse:
        self._record(
            "words",
            level=level,
            language=language,
            count=count,
            exclude_words=list(exclude_words),
        )
        return self._result(self.words or default_words(count))

    async def generate_grammar(self, level: str, language: str) -> GrammarLesson:
        self._record("grammar", level=level, language=language)
        return self._result(self.grammar or default_grammar_lesson())

    async def generate_scramble(
        self, level: str, language: str, *, count: int = tasks.GAME_BATCH_SIZE
    ) -> ScrambleResponse:
        self._record("scramble", level=level, language=language, count=count)
        return self._result(self.scramble or default_scramble_items(count))

    async def generate_gap(
        self, level: str, language: str, *, count: int = tasks.GAME_BATCH_SIZE
    ) -> GapResponse:
        self._record("gap", level=level, language=language, count=count)
        return self._result(self.gap or default_gap_items(count))

    async def generate_race(
        self, level: str, language: str, *, count: int = tasks.GAME_BATCH_SIZE
    ) -> RaceResponse:
        self._record("race", level=level, language=language, count=count)
        return self._result(self.race or default_race_items(count))

    async def generate_truefalse(
        self, level: str, language: str, *, count: int = tasks.GAME_BATCH_SIZE
    ) -> TrueFalseResponse:
        self._record("truefalse", level=level, language=language, count=count)
        return self._result(self.truefalse or default_truefalse_items(count))

    def calls_of(self, task: str) -> list[dict[str, Any]]:
        """Все вызовы одной задачи — удобно проверять в тестах."""
        return [call for call in self.calls if call["task"] == task]

    def _record(self, task: str, **kwargs: Any) -> None:
        self.calls.append({"task": task, **kwargs})

    def _result(self, value: Any) -> Any:
        if self.error is not None:
            raise self.error
        return value
