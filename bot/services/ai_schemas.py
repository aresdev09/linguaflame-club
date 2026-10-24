"""Pydantic-схемы ответов ИИ.

Форматы — из скила `group-content-ai`, без изменений. Всё, что нарушает
схему, отбрасывается: клиент повторит запрос, а сервис возьмёт банк.
"""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

# Обязательный непустой текст.
RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Лимиты Telegram: вопрос влезает в сообщение, вариант — в кнопку.
QuestionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
OptionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class FourOptions(BaseModel):
    """Общая проверка заданий: ровно 4 разных варианта, один верный."""

    options: list[OptionText]
    correct_index: int

    @model_validator(mode="after")
    def _check_options(self) -> "FourOptions":
        if len(self.options) != 4:
            raise ValueError("вариантов должно быть ровно 4")
        if len({option.lower() for option in self.options}) != 4:
            raise ValueError("варианты не должны повторяться")
        if not 0 <= self.correct_index < 4:
            raise ValueError("correct_index должен быть от 0 до 3")
        return self


class QuizQuestion(FourOptions):
    """Вопрос квиза с пояснением правильного ответа."""

    question: QuestionText
    explanation: RequiredText


class QuizBatch(BaseModel):
    """Пачка вопросов квиза — генерируется целиком за один запрос."""

    questions: list[QuizQuestion] = Field(min_length=1)


class WordItem(BaseModel):
    """Слово дня: транскрипция, перевод и пример."""

    word: RequiredText
    transcription: RequiredText
    translation: RequiredText
    example: RequiredText


class WordsResponse(BaseModel):
    """Слова дня; число слов проверяет клиент через валидатор."""

    words: list[WordItem] = Field(min_length=1)


class GrammarExercise(BaseModel):
    """Упражнение в уроке грамматики."""

    question: QuestionText
    options: list[OptionText]
    correct_index: int

    @model_validator(mode="after")
    def _check_options(self) -> "GrammarExercise":
        if not 2 <= len(self.options) <= 6:
            raise ValueError("в упражнении должно быть от 2 до 6 вариантов")
        if len({option.lower() for option in self.options}) != len(self.options):
            raise ValueError("варианты не должны повторяться")
        if not 0 <= self.correct_index < len(self.options):
            raise ValueError("correct_index вне диапазона вариантов")
        return self


class GrammarLesson(BaseModel):
    """Короткий урок грамматики: объяснение, примеры и упражнение."""

    topic: RequiredText
    explanation: RequiredText
    examples: list[RequiredText] = Field(min_length=2, max_length=4)
    exercise: GrammarExercise
    answer_explanation: RequiredText


class ScrambleItem(BaseModel):
    """Слово для игры «Собери слово»: 5–10 букв, без пробелов и дефисов."""

    word: RequiredText
    hint: RequiredText

    @field_validator("word")
    @classmethod
    def _check_word(cls, value: str) -> str:
        if not 5 <= len(value) <= 10:
            raise ValueError("слово должно быть от 5 до 10 букв")
        if not value.isalpha():
            raise ValueError("слово должно состоять только из букв")
        return value.lower()


class ScrambleResponse(BaseModel):
    items: list[ScrambleItem] = Field(min_length=1)


class GapItem(FourOptions):
    """Предложение с одним пропуском `___` и четырьмя вариантами."""

    sentence: QuestionText
    explanation: RequiredText

    @model_validator(mode="after")
    def _check_gap(self) -> "GapItem":
        if self.sentence.count("___") != 1:
            raise ValueError("в предложении должен быть ровно один пропуск ___")
        return self


class GapResponse(BaseModel):
    items: list[GapItem] = Field(min_length=1)


class RaceItem(FourOptions):
    """Слово для гонки переводов и четыре варианта перевода."""

    word: RequiredText


class RaceResponse(BaseModel):
    items: list[RaceItem] = Field(min_length=1)


class TrueFalseItem(BaseModel):
    """Утверждение «правда или ложь» с пояснением."""

    statement: QuestionText
    answer: bool
    explanation: RequiredText


class TrueFalseResponse(BaseModel):
    items: list[TrueFalseItem] = Field(min_length=1)
