"""Мини-игры: меню /games, команды игр и ответы scramble текстом.

Кнопки мини-игр обрабатывает общий роутер квиза (`g:`). Здесь — запуск
игр и единственный текстовый ввод: ответ на сообщение scramble. Privacy
mode пропускает боту только reply на его сообщения, поэтому остальные
тексты группы сюда не попадают.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.keyboards import games_menu
from bot.locales import t
from bot.models import Chat
from bot.sender import Sender
from bot.services.games.base import AnswerOutcome, AnswerResult, StartResult, refusal_key
from bot.services.games.manager import MINI_GAME_KINDS, GameManager

# Команды и кнопки этого роутера работают только в группах.
GROUP_CHAT_TYPES = {"group", "supergroup"}

GAME_MENU_PREFIX = "games:"


def parse_game_menu(data: str | None) -> str | None:
    """Вид игры из callback_data меню; чужие данные — None."""
    if data is None or not data.startswith(GAME_MENU_PREFIX):
        return None
    kind = data[len(GAME_MENU_PREFIX) :]
    return kind if kind in MINI_GAME_KINDS else None


async def games_command(message: Message, lang: str, sender: Sender) -> None:
    """Показывает меню мини-игр."""
    await sender.reply(message, t("games.menu_title", lang), reply_markup=games_menu(lang))


async def scramble_command(
    message: Message, chat: Chat, lang: str, sender: Sender, games: GameManager
) -> None:
    await _start_game_command(message, chat, lang, sender, games, "scramble")


async def gap_command(
    message: Message, chat: Chat, lang: str, sender: Sender, games: GameManager
) -> None:
    await _start_game_command(message, chat, lang, sender, games, "gap")


async def race_command(
    message: Message, chat: Chat, lang: str, sender: Sender, games: GameManager
) -> None:
    await _start_game_command(message, chat, lang, sender, games, "race")


async def truefalse_command(
    message: Message, chat: Chat, lang: str, sender: Sender, games: GameManager
) -> None:
    await _start_game_command(message, chat, lang, sender, games, "truefalse")


async def _start_game_command(
    message: Message,
    chat: Chat,
    lang: str,
    sender: Sender,
    games: GameManager,
    kind: str,
) -> None:
    """Запускает игру из команды и объясняет отказ, если не вышло."""
    if message.from_user is None:
        return
    outcome = await games.start_mini_game(
        chat,
        kind,
        started_by=message.from_user.id,
        thread_id=message.message_thread_id,
    )
    if outcome.result is StartResult.STARTED:
        return  # первый раунд уже в чате
    await sender.reply(message, t(refusal_key(outcome), lang))


async def game_menu_choice(
    callback: CallbackQuery,
    chat: Chat,
    lang: str,
    sender: Sender,
    games: GameManager,
) -> None:
    """Кнопка меню: сразу убираем часики и запускаем выбранную игру."""
    if callback.from_user is None or callback.message is None:
        await sender.answer_callback(callback)
        return
    kind = parse_game_menu(callback.data)
    if kind is None:
        await sender.answer_callback(callback)
        return
    await sender.answer_callback(callback)
    outcome = await games.start_mini_game(
        chat,
        kind,
        started_by=callback.from_user.id,
        thread_id=callback.message.message_thread_id,
    )
    if outcome.result is StartResult.STARTED:
        return
    await sender.reply(callback.message, t(refusal_key(outcome), lang))


async def scramble_answer(
    message: Message, chat: Chat, lang: str, sender: Sender, games: GameManager
) -> None:
    """Текстовый ответ на сообщение scramble: только reply на раунд."""
    if message.from_user is None or message.from_user.is_bot:
        return
    reply = message.reply_to_message
    if reply is None:
        return
    text = (message.text or "").strip()
    if not text:
        return
    outcome = await games.submit_text_answer(
        chat_id=chat.id,
        user_id=message.from_user.id,
        reply_to_message_id=reply.message_id,
        text=text,
    )
    if outcome is None:
        return  # ответ не на текущий раунд игры
    answer = _text_answer(outcome, lang)
    if answer is None:
        return  # раунд уже закрыт: не засоряем чат
    await sender.reply(message, answer)


def _text_answer(outcome: AnswerOutcome, lang: str) -> str | None:
    """Короткий ответ на текстовую догадку."""
    if outcome.result is AnswerResult.CORRECT:
        if outcome.points > 0:
            return t("game.correct", lang, points=outcome.points)
        return t("game.correct_capped", lang)
    if outcome.result is AnswerResult.WRONG:
        return t("game.wrong", lang)
    if outcome.result is AnswerResult.ALREADY:
        return t("game.already_answered", lang)
    if outcome.result is AnswerResult.TOO_LATE:
        return t("game.too_late", lang)
    return None


def build_router() -> Router:
    """Собирает роутер мини-игр."""
    router = Router(name="games")

    router.message.filter(F.chat.type.in_(GROUP_CHAT_TYPES))
    router.callback_query.filter(F.message.chat.type.in_(GROUP_CHAT_TYPES))

    router.message.register(games_command, Command("games"))
    router.message.register(scramble_command, Command("scramble"))
    router.message.register(gap_command, Command("gap"))
    router.message.register(race_command, Command("race"))
    router.message.register(truefalse_command, Command("truefalse"))
    router.callback_query.register(game_menu_choice, F.data.startswith(GAME_MENU_PREFIX))
    router.message.register(
        scramble_answer,
        F.reply_to_message,
        F.text,
        ~F.text.startswith("/"),
    )
    return router
