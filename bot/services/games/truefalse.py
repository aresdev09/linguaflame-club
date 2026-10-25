"""Правда или ложь: утверждение, две кнопки и объяснение после раунда."""

import html
import uuid

from pydantic import ValidationError

from bot.config import Settings
from bot.keyboards import truefalse_keyboard
from bot.locales import t
from bot.models import Chat
from bot.repositories import RoundAnswer, SessionTotal
from bot.services.ai_schemas import TrueFalseItem
from bot.services.games.base import Game, RoundView
from bot.services.games.common import correct_players_line, render_session_results


def truefalse_points(settings: Settings, *, is_correct: bool) -> int:
    """Очки true/false: фиксированная награда каждому верному ответу."""
    return settings.score_truefalse_correct if is_correct else 0


class TrueFalseGame(Game):
    """Правда или ложь: серия утверждений с двумя кнопками."""

    kind = "truefalse"
    duration_setting = "truefalse_round_seconds"

    async def prepare(self, chat: Chat, raw_items: list[dict]) -> list[dict]:
        """Оставляет только валидные утверждения с объяснением."""
        prepared: list[dict] = []
        seen: set[str] = set()
        for raw in raw_items:
            try:
                item = TrueFalseItem.model_validate(raw).model_dump()
            except ValidationError:
                continue
            key = " ".join(item["statement"].lower().split())
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
            t("truefalse.title", lang, current=round_no, total=state["total"]),
            "",
            html.escape(item["statement"]),
            "",
            t("truefalse.footer", lang, seconds=state["duration"]),
        ]
        keyboard = truefalse_keyboard(session_id, round_no, lang)
        return RoundView("\n".join(lines), keyboard)

    def check_answer(self, item: dict, payload: str) -> bool:
        try:
            choice = int(payload)
        except (TypeError, ValueError):
            return False
        if choice not in (0, 1):
            return False
        return bool(choice) is bool(item["answer"])

    def answer_label(self, item: dict, payload: str) -> str:
        try:
            return "true" if int(payload) == 1 else "false"
        except (TypeError, ValueError):
            return (payload or "?")[:10]

    def points_for(
        self,
        item: dict,
        *,
        answer_time_ms: int,
        duration_seconds: int,
        is_correct: bool,
        is_first: bool,
    ) -> int:
        return truefalse_points(self._settings, is_correct=is_correct)

    def render_round_result(
        self, state: dict, round_no: int, answers: list[RoundAnswer], lang: str
    ) -> str:
        item = state["items"][round_no - 1]
        answer = t("truefalse.true", lang) if item["answer"] else t("truefalse.false", lang)
        lines = [
            t("truefalse.title", lang, current=round_no, total=state["total"]),
            "",
            html.escape(item["statement"]),
            "",
            t("truefalse.correct_line", lang, answer=answer),
            t("quiz.explanation_line", lang, explanation=html.escape(item["explanation"])),
            "",
            correct_players_line(answers, lang),
        ]
        return "\n".join(lines)

    def render_results(self, totals: list[SessionTotal], lang: str, flame_days: int | None) -> str:
        return render_session_results("truefalse.finished", totals, lang, flame_days)
