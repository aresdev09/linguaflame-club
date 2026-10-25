"""Гонка переводов: пять слов и четыре варианта перевода кнопками.

Очки зависят от скорости: чем раньше верный ответ, тем больше. Серия
заканчивается после последнего слова, все верные ответы получают очки.
"""

import html
import uuid

from pydantic import ValidationError

from bot.config import Settings
from bot.keyboards import option_buttons_keyboard
from bot.locales import t
from bot.models import Chat
from bot.repositories import RoundAnswer, SessionTotal
from bot.services.ai_schemas import RaceItem
from bot.services.games.base import Game, RoundView
from bot.services.games.common import (
    answer_letter,
    check_choice,
    correct_players_line,
    render_session_results,
)


def race_points(
    settings: Settings,
    *,
    answer_time_ms: int,
    duration_seconds: int,
    is_correct: bool,
) -> int:
    """Очки за слово: максимум сразу, минимум 1 в конце раунда.

    Подозрительно быстрый ответ (быстрее `score_fast_answer_ms`) бонуса
    скорости не получает: остаётся только минимальный 1 балл.
    """
    if not is_correct:
        return 0
    if answer_time_ms < settings.score_fast_answer_ms:
        return 1
    duration_ms = max(duration_seconds, 1) * 1000
    remaining = max(duration_ms - max(answer_time_ms, 0), 0)
    return max(1, int(settings.score_race_word_max * remaining / duration_ms))


class RaceGame(Game):
    """Гонка переводов: серия раундов, очки за скорость."""

    kind = "race"
    duration_setting = "race_round_seconds"

    async def prepare(self, chat: Chat, raw_items: list[dict]) -> list[dict]:
        """Оставляет валидные слова с четырьмя уникальными переводами."""
        prepared: list[dict] = []
        seen: set[str] = set()
        for raw in raw_items:
            try:
                item = RaceItem.model_validate(raw).model_dump()
            except ValidationError:
                continue
            key = " ".join(item["word"].lower().split())
            if key in seen:
                continue
            seen.add(key)
            prepared.append(item)
        return prepared

    def render_round(
        self, state: dict, round_no: int, lang: str, session_id: uuid.UUID
    ) -> RoundView:
        item = state["items"][round_no - 1]
        lines = [
            t("race.title", lang, current=round_no, total=state["total"]),
            "",
            t("race.prompt", lang, word=html.escape(item["word"])),
            "",
            t("race.footer", lang, seconds=state["duration"]),
        ]
        keyboard = option_buttons_keyboard(session_id, round_no, list(item["options"]))
        return RoundView("\n".join(lines), keyboard)

    def check_answer(self, item: dict, payload: str) -> bool:
        return check_choice(item, payload)

    def answer_label(self, item: dict, payload: str) -> str:
        return answer_letter(payload)

    def points_for(
        self,
        item: dict,
        *,
        answer_time_ms: int,
        duration_seconds: int,
        is_correct: bool,
        is_first: bool,
    ) -> int:
        return race_points(
            self._settings,
            answer_time_ms=answer_time_ms,
            duration_seconds=duration_seconds,
            is_correct=is_correct,
        )

    def render_round_result(
        self, state: dict, round_no: int, answers: list[RoundAnswer], lang: str
    ) -> str:
        item = state["items"][round_no - 1]
        correct = html.escape(item["options"][int(item["correct_index"])])
        lines = [
            t("race.title", lang, current=round_no, total=state["total"]),
            "",
            t("race.correct_line", lang, word=html.escape(item["word"]), option=correct),
            "",
            correct_players_line(answers, lang),
        ]
        return "\n".join(lines)

    def render_results(self, totals: list[SessionTotal], lang: str, flame_days: int | None) -> str:
        return render_session_results("race.finished", totals, lang, flame_days)
