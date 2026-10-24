"""Фильтр стоп-слов: контент подходит учебной аудитории любого возраста.

Проверяется каждый элемент перед записью в банк и перед показом группе.
Список стартовый — его можно расширять без изменения логики.
"""

import re

# Недопустимые темы: насилие, наркотики, взрослый контент, оскорбления,
# опасные и политические темы.
STOP_WORDS: frozenset[str] = frozenset(
    {
        # насилие и оружие
        "kill",
        "kills",
        "killed",
        "murder",
        "blood",
        "gun",
        "guns",
        "weapon",
        "weapons",
        "shoot",
        "shooting",
        "war",
        "bomb",
        "terror",
        "terrorist",
        "violence",
        "violent",
        "corpse",
        # курение и наркотики
        "cigarette",
        "cigarettes",
        "smoking",
        "drug",
        "drugs",
        "cocaine",
        "heroin",
        "marijuana",
        # взрослый контент
        "sex",
        "sexual",
        "porn",
        "nude",
        "naked",
        "erotic",
        # оскорбления
        "fuck",
        "fucking",
        "shit",
        "bitch",
        "bastard",
        "asshole",
        # опасные темы
        "suicide",
        "self-harm",
        "racism",
        "racist",
        # политика
        "politics",
        "political",
        "president",
        "election",
        "parliament",
    }
)

_WORD_RE = re.compile(r"[a-z][a-z'’-]*", re.IGNORECASE)


def find_stop_word(text: str) -> str | None:
    """Первое стоп-слово в тексте или None, если текст чистый."""
    for match in _WORD_RE.finditer(text.lower()):
        if match.group() in STOP_WORDS:
            return match.group()
    return None


def is_safe(text: str) -> bool:
    """True, если в тексте нет стоп-слов."""
    return find_stop_word(text) is None


def find_stop_word_in(payload: dict) -> str | None:
    """Ищет стоп-слова во всех строках payload, включая вложенные списки."""
    return find_stop_word(" ".join(_texts(payload)))


def _texts(value: object) -> list[str]:
    """Собирает все строки из значения любой вложенности."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _texts(item)]
    return []
