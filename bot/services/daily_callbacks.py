"""Формат кнопок под постом дня: «Got it» и «Show answer».

В `callback_data` только действие и id записи `daily_posts`: правильный
ответ упражнения в кнопку не попадает, он лежит в базе. Пример: `daily:got:17`.
"""

# Префикс кнопок поста дня; игры используют `g:`, настройки — `set:`.
PREFIX = "daily:"

# Действия кнопок.
ACTION_GOT = "got"
ACTION_SHOW = "show"


def build_got_callback(post_id: int) -> str:
    """callback_data кнопки «Got it»."""
    return f"{PREFIX}{ACTION_GOT}:{post_id}"


def build_show_callback(post_id: int) -> str:
    """callback_data кнопки «Show answer»."""
    return f"{PREFIX}{ACTION_SHOW}:{post_id}"


def parse_daily_callback(data: str | None) -> tuple[str, int] | None:
    """Разбирает callback_data поста дня в `(действие, id поста)`.

    Чужие и повреждённые данные — None: на них бот отвечает молча.
    """
    if data is None or not data.startswith(PREFIX):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    action, raw_id = parts[1], parts[2]
    if action not in (ACTION_GOT, ACTION_SHOW):
        return None
    try:
        return action, int(raw_id)
    except ValueError:
        return None
