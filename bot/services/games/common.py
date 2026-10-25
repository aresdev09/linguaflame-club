"""Общие мелочи мини-игр: разбор вариантов, итоги раунда и сессии.

Движок раундов живёт в `base.py`; здесь только чистые функции, которые
одинаково нужны нескольким играм. Никаких aiogram-объектов.
"""

from bot.locales import t
from bot.repositories import RoundAnswer, SessionTotal
from bot.services.games.callbacks import OPTION_LETTERS
from bot.services.scoring import MEDALS, clean_display_name


def choice_index(payload: str) -> int | None:
    """Индекс варианта из callback_data; мусор превращается в None."""
    try:
        return int(payload)
    except (TypeError, ValueError):
        return None


def check_choice(item: dict, payload: str, *, options: int = 4) -> bool:
    """Верен ли индекс варианта; вне диапазона — неверный ответ."""
    index = choice_index(payload)
    if index is None or not 0 <= index < options:
        return False
    return index == int(item["correct_index"])


def answer_letter(payload: str) -> str:
    """Буква варианта для журнала ответов."""
    index = choice_index(payload)
    if index is not None and 0 <= index < len(OPTION_LETTERS):
        return OPTION_LETTERS[index]
    return (payload or "?")[:10]


def correct_players_line(answers: list[RoundAnswer], lang: str, *, first_only: bool = False) -> str:
    """Строка «кто ответил верно»; для scramble — только первый победитель."""
    if first_only:
        winner = next((answer for answer in answers if answer.is_correct), None)
        chosen = [winner] if winner is not None else []
    else:
        chosen = [answer for answer in answers if answer.is_correct]
    if not chosen:
        return t("game.nobody_correct", lang)
    names = [clean_display_name(answer.display_name, answer.user_id, lang) for answer in chosen]
    return t("game.correct_players", lang, names=", ".join(names))


def render_session_results(
    title_key: str,
    totals: list[SessionTotal],
    lang: str,
    flame_days: int | None,
) -> str:
    """Итоги любой игры: топ сессии и огонёк группы."""
    lines = [t(title_key, lang), ""]
    scorers = [total for total in totals if total.points > 0]
    if scorers:
        for place, total in enumerate(scorers, start=1):
            mark = MEDALS[place - 1] if place <= len(MEDALS) else f"{place}."
            lines.append(
                t(
                    "top.entry",
                    lang,
                    place=mark,
                    name=clean_display_name(total.display_name, total.user_id, lang),
                    points=total.points,
                )
            )
    else:
        lines.append(t("game.results_empty", lang))
    if flame_days is not None:
        lines += ["", t("game.flame", lang, days=flame_days)]
    return "\n".join(lines)
