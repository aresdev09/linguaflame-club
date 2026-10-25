"""Групповой квиз: команда `/quiz` и кнопки ответов A–D.

Кнопки несут только сессию, раунд и индекс варианта. На любое нажатие
бот отвечает `callback.answer()` — даже на отказы, иначе у игрока
висят «часики», а группа не понимает, что произошло.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.locales import t
from bot.models import Chat
from bot.sender import Sender
from bot.services.games.base import AnswerOutcome, AnswerResult, StartResult, refusal_key
from bot.services.games.callbacks import parse_answer_callback
from bot.services.games.manager import GameManager

# Команды и кнопки этого роутера работают только в группах.
GROUP_CHAT_TYPES = {"group", "supergroup"}

# Сколько раундов по умолчанию и максимум в /quiz [n].
DEFAULT_ROUNDS = 5
MAX_ROUNDS = 10


def parse_rounds(
    text: str | None, *, default: int = DEFAULT_ROUNDS, maximum: int = MAX_ROUNDS
) -> int:
    """Число раундов из «/quiz 7»: по умолчанию 5, максимум 10.

    Некорректное значение («/quiz abc», «/quiz 0») не ругается — играем
    стандартную длину.
    """
    if text is None:
        return default
    parts = text.split()
    if len(parts) < 2:
        return default
    try:
        rounds = int(parts[1])
    except ValueError:
        return default
    if rounds < 1:
        return default
    return min(rounds, maximum)


async def quiz_command(
    message: Message,
    chat: Chat,
    lang: str,
    sender: Sender,
    games: GameManager,
) -> None:
    """Запускает квиз; сообщение первого раунда отправляет движок."""
    if message.from_user is None:
        return
    rounds = parse_rounds(message.text)
    outcome = await games.start_quiz(
        chat,
        started_by=message.from_user.id,
        rounds=rounds,
        thread_id=message.message_thread_id,
    )
    if outcome.result is StartResult.STARTED:
        return  # первый раунд уже в чате
    await sender.reply(message, t(refusal_key(outcome), lang))


async def game_answer(
    callback: CallbackQuery,
    chat: Chat,
    lang: str,
    sender: Sender,
    games: GameManager,
) -> None:
    """Ответ на кнопках: движок решает, ответ всегда убирает «часики»."""
    if callback.message is None:
        await sender.answer_callback(callback)
        return
    parsed = parse_answer_callback(callback.data)
    if parsed is None:
        await sender.answer_callback(callback)  # чужие данные — молча
        return
    session_id, round_no, choice = parsed
    outcome = await games.submit_answer(
        session_id,
        chat_id=chat.id,
        user_id=callback.from_user.id,
        round_no=round_no,
        payload=str(choice),
    )
    await sender.answer_callback(callback, _answer_text(outcome, lang))


def _answer_text(outcome: AnswerOutcome, lang: str) -> str | None:
    """Короткий ответ на нажатие: очки или причина отказа."""
    if outcome.result is AnswerResult.CORRECT:
        if outcome.points > 0:
            return t("game.correct", lang, points=outcome.points)
        return t("game.correct_capped", lang)  # дневной лимит очков
    if outcome.result is AnswerResult.WRONG:
        return t("game.wrong", lang)
    if outcome.result is AnswerResult.ALREADY:
        return t("game.already_answered", lang)
    if outcome.result is AnswerResult.TOO_LATE:
        return t("game.too_late", lang)
    if outcome.result is AnswerResult.ROUND_OVER:
        return t("game.round_over", lang)
    return t("game.no_game", lang)


def build_router() -> Router:
    """Собирает роутер квиза."""
    router = Router(name="quiz")

    router.message.filter(F.chat.type.in_(GROUP_CHAT_TYPES))
    router.callback_query.filter(F.message.chat.type.in_(GROUP_CHAT_TYPES))

    router.message.register(quiz_command, Command("quiz"))
    router.callback_query.register(game_answer, F.data.startswith("g:"))
    return router
