"""Inline-клавиатуры бота."""

import uuid
from datetime import time as dt_time

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import (
    LEVELS,
    POPULAR_TIMEZONES,
    POST_TIME_OPTIONS,
    QUIET_HOURS_OPTIONS,
    TRANSLATION_LANGUAGES,
    UI_LANGUAGES,
    WORDS_COUNT_OPTIONS,
)
from bot.locales import t
from bot.models import Chat
from bot.services.daily_callbacks import build_got_callback, build_show_callback
from bot.services.games.callbacks import OPTION_LETTERS, build_answer_callback


def _mark(selected: bool, label: str) -> str:
    """Помечает текущее значение галочкой."""
    return f"✅ {label}" if selected else label


def _fmt(value: dt_time) -> str:
    return value.strftime("%H:%M")


def settings_menu(chat: Chat, lang: str) -> InlineKeyboardMarkup:
    """Клавиатура меню /setup: текущие значения помечены галочкой."""
    builder = InlineKeyboardBuilder()

    for level in LEVELS:
        builder.button(
            text=_mark(level == chat.level, level), callback_data=f"set:level:{level}"
        )

    builder.button(
        text=_mark(chat.daily_words, t("setup.words", lang)), callback_data="set:words"
    )
    builder.button(
        text=_mark(chat.daily_grammar, t("setup.grammar", lang)), callback_data="set:grammar"
    )

    for count in WORDS_COUNT_OPTIONS:
        builder.button(
            text=_mark(count == chat.words_count, str(count)), callback_data=f"set:count:{count}"
        )

    for code in UI_LANGUAGES:
        builder.button(
            text=_mark(code == chat.ui_language, t(f"lang.{code}", lang)),
            callback_data=f"set:ui:{code}",
        )

    for code in TRANSLATION_LANGUAGES:
        builder.button(
            text=_mark(code == chat.translation_language, t(f"lang.{code}", lang)),
            callback_data=f"set:translation:{code}",
        )

    for zone in POPULAR_TIMEZONES:
        builder.button(
            text=_mark(zone == chat.timezone, zone), callback_data=f"set:timezone:{zone}"
        )
    builder.button(
        text=t("setup.timezone_manual", lang), callback_data="set:manual:timezone"
    )

    current_time = _fmt(chat.daily_time)
    for post_time in POST_TIME_OPTIONS:
        builder.button(
            text=_mark(post_time == current_time, post_time),
            callback_data=f"set:time:{post_time}",
        )
    builder.button(text=t("setup.time_manual", lang), callback_data="set:manual:time")

    quiet_current = (_fmt(chat.quiet_from), _fmt(chat.quiet_to))
    for quiet_from, quiet_to in QUIET_HOURS_OPTIONS:
        builder.button(
            text=_mark(quiet_current == (quiet_from, quiet_to), f"{quiet_from}–{quiet_to}"),
            callback_data=f"set:quiet:{quiet_from}-{quiet_to}",
        )
    builder.button(
        text=_mark(chat.quiet_from == chat.quiet_to, t("setup.off", lang)),
        callback_data="set:quiet:off",
    )

    builder.adjust(6, 2, 3, 2, 6, 3, 3, 1, 4, 1, 4)
    return builder.as_markup()


def top_period_keyboard(lang: str, period: str) -> InlineKeyboardMarkup:
    """Клавиатура /top: переключатель «This week / All time»."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_mark(period == "week", t("top.period_week", lang)),
        callback_data="top:week",
    )
    builder.button(
        text=_mark(period == "all", t("top.period_all", lang)),
        callback_data="top:all",
    )
    builder.adjust(2)
    return builder.as_markup()


def quiz_answers_keyboard(
    session_id: uuid.UUID, round_no: int, options_count: int
) -> InlineKeyboardMarkup:
    """Кнопки ответов квиза: буквы A–D сеткой 2×2.

    В `callback_data` — сессия, раунд и индекс варианта; правильный
    ответ туда не попадает.
    """
    builder = InlineKeyboardBuilder()
    for index in range(options_count):
        builder.button(
            text=OPTION_LETTERS[index],
            callback_data=build_answer_callback(session_id, round_no, index),
        )
    builder.adjust(2)
    return builder.as_markup()


def option_buttons_keyboard(
    session_id: uuid.UUID, round_no: int, labels: list[str]
) -> InlineKeyboardMarkup:
    """Кнопки мини-игр с текстом варианта: gap и race.

    В `callback_data` только индекс, поэтому правильный ответ не виден
    в данных кнопки.
    """
    builder = InlineKeyboardBuilder()
    for index, label in enumerate(labels):
        builder.button(
            text=label,
            callback_data=build_answer_callback(session_id, round_no, index),
        )
    builder.adjust(2)
    return builder.as_markup()


def truefalse_keyboard(session_id: uuid.UUID, round_no: int, lang: str) -> InlineKeyboardMarkup:
    """Две кнопки «Правда» и «Ложь»: 1 — true, 0 — false."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t("truefalse.true", lang),
        callback_data=build_answer_callback(session_id, round_no, 1),
    )
    builder.button(
        text=t("truefalse.false", lang),
        callback_data=build_answer_callback(session_id, round_no, 0),
    )
    builder.adjust(2)
    return builder.as_markup()


def daily_words_keyboard(post_id: int, lang: str) -> InlineKeyboardMarkup:
    """Кнопка «Got it» под постом слов дня."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t("daily.got", lang), callback_data=build_got_callback(post_id))
    return builder.as_markup()


def daily_grammar_keyboard(post_id: int, lang: str) -> InlineKeyboardMarkup:
    """Кнопка «Show answer» под постом грамматики дня."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t("daily.show_answer", lang), callback_data=build_show_callback(post_id)
    )
    return builder.as_markup()


def games_menu(lang: str) -> InlineKeyboardMarkup:
    """Меню /games: по кнопке на каждую мини-игру."""
    builder = InlineKeyboardBuilder()
    for kind in ("scramble", "gap", "race", "truefalse"):
        builder.button(
            text=t(f"game.{kind}", lang),
            callback_data=f"games:{kind}",
        )
    builder.adjust(2)
    return builder.as_markup()
