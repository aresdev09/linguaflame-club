"""Пропущенное слово: предложение с ___ и четыре варианта кнопками."""

import html
import uuid

from pydantic import ValidationError

from bot.config import Settings
from bot.keyboards import option_buttons_keyboard
from bot.locales import t
from bot.models import Chat
from bot.repositories import RoundAnswer, SessionTotal
from bot.services.ai_schemas import GapItem
from bot.services.games.base import Game, RoundView
from bot.services.games.common import (
    answer_letter,
    check_choice,
    correct_players_line,
    render_session_results,
)


def gap_points(
    settings: Settings,
    *,
    is_correct: bool,
    is_first: bool,
) -> int:
    """Очки gap: база за верный ответ и бонус первому."""
    if not is_correct:
        return 0
    points = settings.score_gap_correct
    if is_first:
        points += settings.score_quiz_first_bonus
    return points


class GapGame(Game):
    """Пропущенное слово: один раунд с четырьмя вариантами."""

    kind = "gap"
    duration_setting = "gap_round_seconds"

    async def prepare(self, chat: Chat, raw_items: list[dict]) -> list[dict]:
        """Оставляет только валидные предложения с одним пропуском."""
        prepared: list[dict] = []
        seen: set[str] = set()
        for raw in raw_items:
            try:
                item = GapItem.model_validate(raw).model_dump()
            except ValidationError:
                continue
            key = " ".join(item["sentence"].lower().split())
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
            t("gap.title", lang, current=round_no, total=state["total"]),
            "",
            html.escape(item["sentence"]),
            "",
            t("gap.footer", lang, seconds=state["duration"]),
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
        return gap_points(self._settings, is_correct=is_correct, is_first=is_first)

    def render_round_result(
        self, state: dict, round_no: int, answers: list[RoundAnswer], lang: str
    ) -> str:
        item = state["items"][round_no - 1]
        correct = html.escape(item["options"][int(item["correct_index"])])
        lines = [
            t("gap.title", lang, current=round_no, total=state["total"]),
            "",
            html.escape(item["sentence"]),
            "",
            t("gap.correct_line", lang, option=correct),
            t("quiz.explanation_line", lang, explanation=html.escape(item["explanation"])),
            "",
            correct_players_line(answers, lang),
        ]
        return "\n".join(lines)

    def render_results(self, totals: list[SessionTotal], lang: str, flame_days: int | None) -> str:
        return render_session_results("gap.finished", totals, lang, flame_days)
