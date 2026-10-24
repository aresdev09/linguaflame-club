"""Формат игровых кнопок: callback_data и подписи вариантов.

В `callback_data` только сессия, раунд и индекс варианта — правильный
ответ в кнопку не попадает. Пример: `g:550e8400-e29b…:3:1`.
"""

import uuid

# Префикс игровых кнопок; настройки чата используют `set:`, рейтинг — `top:`.
PREFIX = "g:"

# Подписи вариантов A–D: и на кнопках, и в тексте раунда.
OPTION_LETTERS: tuple[str, ...] = ("A", "B", "C", "D")


def build_answer_callback(session_id: uuid.UUID, round_no: int, choice: int) -> str:
    """Собирает callback_data кнопки ответа."""
    return f"{PREFIX}{session_id}:{round_no}:{choice}"


def parse_answer_callback(data: str | None) -> tuple[uuid.UUID, int, int] | None:
    """Разбирает callback_data кнопки ответа.

    Возвращает `(session_id, round, choice)` или None для чужих
    и повреждённых данных — на них бот отвечает молча.
    """
    if data is None or not data.startswith(PREFIX):
        return None
    parts = data.split(":")
    if len(parts) != 4:
        return None
    try:
        return uuid.UUID(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None
