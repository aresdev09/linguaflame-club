"""Настройки группы: сводка для меню и применение изменений.

Логика без aiogram: роутер только парсит нажатие и рисует результат.
"""

from datetime import time as dt_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import (
    LEVELS,
    TRANSLATION_LANGUAGES,
    UI_LANGUAGES,
    WORDS_COUNT_OPTIONS,
)
from bot.locales import t
from bot.models import Chat
from bot.repositories import ChatRepo


def settings_summary(chat: Chat, lang: str) -> str:
    """Текст меню настроек с текущими значениями группы."""
    words = t("setup.on", lang) if chat.daily_words else t("setup.off", lang)
    grammar = t("setup.on", lang) if chat.daily_grammar else t("setup.off", lang)
    if chat.quiet_from == chat.quiet_to:
        quiet = t("setup.off", lang)
    else:
        quiet = f"{_fmt_time(chat.quiet_from)}–{_fmt_time(chat.quiet_to)}"
    lines = [
        t("setup.header", lang),
        "",
        f"{t('setup.level', lang)}: {chat.level}",
        f"{t('setup.words', lang)}: {words}",
        f"{t('setup.grammar', lang)}: {grammar}",
        f"{t('setup.words_count', lang)}: {chat.words_count}",
        f"{t('setup.ui_language', lang)}: {_language_name(chat.ui_language, lang)}",
        f"{t('setup.translation_language', lang)}: "
        f"{_language_name(chat.translation_language, lang)}",
        f"{t('setup.timezone', lang)}: {chat.timezone}",
        f"{t('setup.daily_time', lang)}: {_fmt_time(chat.daily_time)}",
        f"{t('setup.quiet', lang)}: {quiet}",
        "",
        t("setup.hint", lang),
    ]
    return "\n".join(lines)


async def apply_callback_setting(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    data: str,
) -> None:
    """Применяет настройку из callback_data (формат `set:<поле>:<значение>`).

    Неизвестные или недопустимые значения молча игнорируются: кнопки
    рисует наш бот, а настройки в базе — источник правды.
    """
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""

    if action in ("words", "grammar"):
        field = "daily_words" if action == "words" else "daily_grammar"
        await _toggle(session_factory, chat_id, field)
        return

    updates: dict[str, object]
    if action == "level" and value in LEVELS:
        updates = {"level": value}
    elif action == "count" and value.isdigit() and int(value) in WORDS_COUNT_OPTIONS:
        updates = {"words_count": int(value)}
    elif action == "ui" and value in UI_LANGUAGES:
        updates = {"ui_language": value}
    elif action == "translation" and value in TRANSLATION_LANGUAGES:
        updates = {"translation_language": value}
    elif action == "timezone" and is_valid_timezone(value):
        updates = {"timezone": value}
    elif action == "time":
        parsed_time = parse_time(value)
        if parsed_time is None:
            return
        updates = {"daily_time": parsed_time}
    elif action == "quiet":
        quiet = parse_quiet(value)
        if quiet is None:
            return
        updates = {"quiet_from": quiet[0], "quiet_to": quiet[1]}
    else:
        return

    await _update(session_factory, chat_id, updates)


async def apply_manual_setting(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    field: str,
    raw_value: str,
) -> str | None:
    """Применяет значение, введённое вручную; возвращает ключ ошибки или None."""
    value = raw_value.strip()
    if field == "timezone":
        if not is_valid_timezone(value):
            return "setup.bad_timezone"
        updates: dict[str, object] = {"timezone": value}
    elif field == "time":
        parsed_time = parse_time(value)
        if parsed_time is None:
            return "setup.bad_time"
        updates = {"daily_time": parsed_time}
    else:
        return None
    await _update(session_factory, chat_id, updates)
    return None


def is_valid_timezone(value: str) -> bool:
    """Проверяет часовой пояс через базу zoneinfo."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def parse_time(value: str) -> dt_time | None:
    """Разбирает время ``HH:MM``; недопустимое — None."""
    try:
        hour_text, minute_text = value.strip().split(":")
        hour, minute = int(hour_text), int(minute_text)
    except ValueError:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return dt_time(hour, minute)
    return None


def parse_quiet(value: str) -> tuple[dt_time, dt_time] | None:
    """Разбирает тихие часы ``HH:MM-HH:MM`` или ``off``."""
    if value == "off":
        return dt_time(0, 0), dt_time(0, 0)
    parts = value.split("-")
    if len(parts) != 2:
        return None
    start, end = parse_time(parts[0]), parse_time(parts[1])
    if start is None or end is None:
        return None
    return start, end


async def _update(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    updates: dict[str, object],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await ChatRepo(session).update_settings(chat_id, **updates)


async def _toggle(
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    field: str,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            repo = ChatRepo(session)
            chat = await repo.get(chat_id)
            if chat is None:
                return
            await repo.update_settings(chat_id, **{field: not getattr(chat, field)})


def _fmt_time(value: dt_time) -> str:
    return value.strftime("%H:%M")


def _language_name(code: str, lang: str) -> str:
    try:
        return t(f"lang.{code}", lang)
    except KeyError:
        return code
