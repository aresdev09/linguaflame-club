"""Групповой квиз: вопросы с четырьмя вариантами A–D.

Очки за верный ответ: `score_quiz_correct`, бонус скорости до
`score_quiz_speed_bonus_max` (линейно по остатку времени раунда) и
дополнительно `score_quiz_first_bonus` первому верному. Ответы быстрее
`score_fast_answer_ms` бонус скорости не получают — это подозрительно
быстро, и анти-обман важнее пары очков.
"""

import html
import logging
import uuid

from pydantic import ValidationError

from bot.config import Settings
from bot.keyboards import quiz_answers_keyboard
from bot.locales import t
from bot.models import Chat
from bot.repositories import RoundAnswer, SessionTotal
from bot.services.ai_schemas import QuizQuestion
from bot.services.games.base import Game, RoundView
from bot.services.games.callbacks import OPTION_LETTERS
from bot.services.scoring import MEDALS, clean_display_name

logger = logging.getLogger(__name__)


def quiz_points(
    settings: Settings,
    *,
    answer_time_ms: int,
    duration_seconds: int,
    is_correct: bool,
    is_first: bool,
) -> int:
    """Очки за ответ в квизе — чистая функция, легко покрыть тестами."""
    if not is_correct:
        return 0
    points = settings.score_quiz_correct
    if answer_time_ms >= settings.score_fast_answer_ms:
        duration_ms = max(duration_seconds, 1) * 1000
        remaining = max(duration_ms - answer_time_ms, 0)
        # Линейно: сразу после показа — максимум, в последнюю секунду — ноль.
        points += int(settings.score_quiz_speed_bonus_max * remaining / duration_ms)
    if is_first:
        points += settings.score_quiz_first_bonus
    return points


class QuizGame(Game):
    """Классический квиз: раунды с кнопками A–D и пояснением после."""

    kind = "quiz"

    async def prepare(self, chat: Chat, raw_items: list[dict]) -> list[dict]:
        """Пропускает контент через схему: 4 разных варианта, один верный."""
        prepared: list[dict] = []
        seen: set[str] = set()
        for raw in raw_items:
            try:
                item = QuizQuestion.model_validate(raw).model_dump()
            except ValidationError as exc:
                logger.warning("Вопрос квиза не прошёл схему: %s", exc)
                continue
            key = item["question"].strip().lower()
            if key in seen:  # одинаковые вопросы не показываем дважды
                continue
            seen.add(key)
            prepared.append(item)
        return prepared

    def render_round(
        self, state: dict, round_no: int, lang: str, session_id: uuid.UUID
    ) -> RoundView:
        item = state["items"][round_no - 1]
        lines = [
            t("quiz.title", lang, current=round_no, total=state["total"]),
            "",
            html.escape(item["question"]),
            "",
        ]
        lines += [
            f"{letter}. {html.escape(option)}"
            for letter, option in zip(OPTION_LETTERS, item["options"], strict=False)
        ]
        lines += ["", t("quiz.footer", lang, seconds=state["duration"])]
        keyboard = quiz_answers_keyboard(session_id, round_no, len(item["options"]))
        return RoundView("\n".join(lines), keyboard)

    def check_answer(self, item: dict, payload: str) -> bool:
        try:
            choice = int(payload)
        except (TypeError, ValueError):
            return False
        return choice == int(item["correct_index"])

    def answer_label(self, item: dict, payload: str) -> str:
        """Буква выбранного варианта: коротко и не таит правильный ответ."""
        try:
            index = int(payload)
        except (TypeError, ValueError):
            return (payload or "?")[:10]
        if 0 <= index < len(OPTION_LETTERS):
            return OPTION_LETTERS[index]
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
        return quiz_points(
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
        correct_index = int(item["correct_index"])
        lines = [
            t("quiz.title", lang, current=round_no, total=state["total"]),
            "",
            html.escape(item["question"]),
            "",
            t(
                "quiz.correct_line",
                lang,
                letter=OPTION_LETTERS[correct_index],
                option=html.escape(item["options"][correct_index]),
            ),
            t("quiz.explanation_line", lang, explanation=html.escape(item["explanation"])),
            "",
        ]
        names = [
            clean_display_name(answer.display_name, answer.user_id, lang)
            for answer in answers
            if answer.is_correct
        ]
        if names:
            lines.append(t("game.correct_players", lang, names=", ".join(names)))
        else:
            lines.append(t("game.nobody_correct", lang))
        return "\n".join(lines)

    def render_results(self, totals: list[SessionTotal], lang: str, flame_days: int | None) -> str:
        lines = [t("quiz.finished", lang), ""]
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
