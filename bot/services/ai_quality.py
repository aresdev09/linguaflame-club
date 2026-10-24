"""Проверки качества контента перед показом.

Схемы ловят структуру, здесь — смысловые правила пачки: число слов,
дубликаты внутри пачки и стабильный хеш элемента для истории чата.
Любое нарушение — элемент отбрасывается, а не чинится «на лету».
"""

import hashlib
import json
from collections.abc import Callable

from bot.services.ai_schemas import WordsResponse

# Поле, по которому элемент считается «тем же самым» при дедупликации.
KEY_FIELDS: dict[str, str] = {
    "quiz_batch": "question",
    "words": "word",
    "scramble": "word",
    "gap": "sentence",
    "race": "word",
    "truefalse": "statement",
}


def require_word_count(count: int) -> Callable[[WordsResponse], None]:
    """Валидатор ответа со словами: ровно count уникальных слов."""

    def _validate(response: WordsResponse) -> None:
        if len(response.words) != count:
            raise ValueError(f"нужно ровно {count} слов, получено {len(response.words)}")
        words = [item.word.lower() for item in response.words]
        if len(set(words)) != len(words):
            raise ValueError("слова в наборе повторяются")

    return _validate


def item_key(kind: str, payload: dict) -> str:
    """Нормализованный ключ элемента: регистр и лишние пробелы не важны."""
    field = KEY_FIELDS.get(kind)
    if field is None:
        return ""
    value = str(payload.get(field, ""))
    return " ".join(value.lower().split())


def dedupe_payloads(kind: str, payloads: list[dict]) -> list[dict]:
    """Убирает повторы внутри пачки, сохраняя порядок."""
    seen: set[str] = set()
    result: list[dict] = []
    for payload in payloads:
        key = item_key(kind, payload)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(payload)
    return result


def content_hash(kind: str, payload: dict) -> str:
    """Стабильный хеш элемента для таблицы chat_content_history."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{kind}:{canonical}".encode()).hexdigest()
