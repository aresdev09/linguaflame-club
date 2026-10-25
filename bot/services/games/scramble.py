"""Собери слово: перемешанные буквы и ответ reply на сообщение игры.

Правила: первый верный ответ забирает очки и сразу закрывает раунд.
Ответы сравниваются без учёта регистра и пробелов. Если за отведённое
время никто не угадал, слово открывается по таймеру.
"""

import html
import random
import uuid

from pydantic import ValidationError

from bot.config import Settings
from bot.locales import t
from bot.models import Chat
from bot.repositories import RoundAnswer, SessionTotal
from bot.services.ai_schemas import ScrambleItem
from bot.services.games.base import Game, RoundView
from bot.services.games.common import correct_players_line, render_session_results


def normalize_word(value: str) -> str:
    """Сравнение слов: без регистра и пробелов."""
    return "".join(value.lower().split())


def scramble_word(word: str) -> str:
    """Детерминированно перемешивает буквы, чтобы они не совпали с словом."""
    letters = list(word)
    if len(letters) < 2:
        return word
    shuffled = letters[:]
    for attempt in range(1, 25):
        random.Random(f"{word}:{attempt}").shuffle(shuffled)
        if shuffled != letters:
            return "".join(shuffled)
    return "".join(reversed(letters))  # слово из одинаковых букв не перемешать


def scramble_points(
    settings: Settings,
    *,
    answer_time_ms: int,
    duration_seconds: int,
    is_correct: bool,
    is_first: bool,
) -> int:
    """Очки scramble: только первый верный ответ, база плюс скорость."""
    if not is_correct or not is_first:
        return 0
    points = settings.score_scramble_correct
    if answer_time_ms >= settings.score_fast_answer_ms:
        duration_ms = max(duration_seconds, 1) * 1000
        remaining = max(duration_ms - answer_time_ms, 0)
        points += int(settings.score_quiz_speed_bonus_max * remaining / duration_ms)
    return points


class ScrambleGame(Game):
    """Собери слово: один раунд, ответ текстом в reply."""

    kind = "scramble"
    duration_setting = "scramble_round_seconds"
    close_on_correct = True
    accepts_text = True

    async def prepare(self, chat: Chat, raw_items: list[dict]) -> list[dict]:
        """Проверяет слово и сразу сохраняет перемешанный вариант."""
        prepared: list[dict] = []
        seen: set[str] = set()
        for raw in raw_items:
            try:
                item = ScrambleItem.model_validate(raw).model_dump()
            except ValidationError:
                continue
            key = normalize_word(item["word"])
            if key in seen:
                continue
            seen.add(key)
            item["scrambled"] = scramble_word(item["word"])
            prepared.append(item)
        return prepared

    def render_round(
        self, state: dict, round_no: int, lang: str, session_id: uuid.UUID
    ) -> RoundView:
        item = state["items"][round_no - 1]
        letters = " ".join(item["scrambled"].upper())
        lines = [
            t("scramble.title", lang, current=round_no, total=state["total"]),
            "",
            t("scramble.letters", lang, letters=html.escape(letters)),
            t("scramble.hint", lang, hint=html.escape(item["hint"])),
            "",
            t("scramble.footer", lang, seconds=state["duration"]),
        ]
        return RoundView("\n".join(lines), None)

    def check_answer(self, item: dict, payload: str) -> bool:
        return normalize_word(payload) == normalize_word(item["word"])

    def answer_label(self, item: dict, payload: str) -> str:
        return " ".join((payload or "").split())[:100]

    def points_for(
        self,
        item: dict,
        *,
        answer_time_ms: int,
        duration_seconds: int,
        is_correct: bool,
        is_first: bool,
    ) -> int:
        return scramble_points(
            self._settings,
            answer_time_ms=answer_time_ms,
            duration_seconds=duration_seconds,
            is_correct=is_correct,
            is_first=is_first,
        )

    def render_round_result(
        self, state: dict, round_no: int, answers: list[RoundAnswer], lang: str
    ) -> str:
        item = state["items"][round_no - 1]
        lines = [
            t("scramble.title", lang, current=round_no, total=state["total"]),
            "",
            t("scramble.correct_line", lang, word=html.escape(item["word"])),
            "",
            correct_players_line(answers, lang, first_only=True),
        ]
        return "\n".join(lines)

    def render_results(self, totals: list[SessionTotal], lang: str, flame_days: int | None) -> str:
        return render_session_results("scramble.finished", totals, lang, flame_days)
