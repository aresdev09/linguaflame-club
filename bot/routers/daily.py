"""Слова и грамматика дня: команды `/word`, `/grammar` и кнопки под постом.

Пост собирается из общего кеша контента: если сегодняшний пост уже
сохранён в `daily_posts` (командой или планировщиком), повторная команда
показывает его без обращения к ИИ. Кнопки несут только действие и id
записи — правильный ответ упражнения в callback_data не попадает.

Роутер собирается фабрикой `build_router()`: обработчики остаются
обычными функциями, их удобно вызывать в тестах напрямую.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.keyboards import daily_grammar_keyboard, daily_words_keyboard
from bot.locales import t
from bot.models import Chat
from bot.sender import Sender
from bot.services.daily import (
    KIND_GRAMMAR,
    KIND_WORDS,
    DailyPostResult,
    DailyService,
    no_content_key,
    tap_key,
)
from bot.services.daily_callbacks import (
    ACTION_GOT,
    ACTION_SHOW,
    parse_daily_callback,
)
from bot.services.daily_callbacks import (
    PREFIX as DAILY_PREFIX,
)

# Команды и кнопки этого роутера работают только в группах.
GROUP_CHAT_TYPES = {"group", "supergroup"}


async def word_command(
    message: Message, chat: Chat, lang: str, sender: Sender, daily: DailyService
) -> None:
    """Показывает слова дня; повтор в тот же день — из сохранённого поста."""
    await _show_post(message, chat, lang, sender, daily, KIND_WORDS)


async def grammar_command(
    message: Message, chat: Chat, lang: str, sender: Sender, daily: DailyService
) -> None:
    """Показывает грамматику дня; повтор — из сохранённого поста."""
    await _show_post(message, chat, lang, sender, daily, KIND_GRAMMAR)


async def _show_post(
    message: Message,
    chat: Chat,
    lang: str,
    sender: Sender,
    daily: DailyService,
    kind: str,
) -> None:
    """Отправляет пост дня: сообщения по частям, кнопка — на последнем."""
    result = await daily.words(chat) if kind == KIND_WORDS else await daily.grammar(chat)
    if not result.chunks:
        await sender.reply(message, t(no_content_key(result.reason), lang))
        return

    keyboard = _keyboard(kind, result, lang)
    last_index = len(result.chunks) - 1
    last_message_id: int | None = None
    for index, chunk in enumerate(result.chunks):
        markup = keyboard if index == last_index else None
        if index == 0:
            sent = await sender.reply(message, chunk, reply_markup=markup)
        else:
            sent = await sender.send(
                chat.id,
                chunk,
                thread_id=message.message_thread_id,
                reply_markup=markup,
            )
        if sent is not None:
            last_message_id = sent.message_id
    await daily.mark_sent(result.post_id, last_message_id)


async def daily_button(
    callback: CallbackQuery, chat: Chat, lang: str, sender: Sender, daily: DailyService
) -> None:
    """«Got it» начисляет очки, «Show answer» открывает ответ упражнения."""
    parsed = parse_daily_callback(callback.data)
    if parsed is None or callback.message is None or callback.from_user is None:
        await sender.answer_callback(callback)
        return

    action, post_id = parsed
    if action == ACTION_SHOW:
        text = await daily.grammar_answer(chat, post_id)
        if text is None:
            await sender.answer_callback(callback, t("daily.stale", lang))
            return
        await sender.edit(callback.message.chat.id, callback.message.message_id, text)
        await sender.answer_callback(callback)
        return

    if action == ACTION_GOT:
        outcome = await daily.claim_points(chat, callback.from_user.id, post_id)
        await sender.answer_callback(callback, t(tap_key(outcome), lang, points=daily.bonus_points))
        return

    await sender.answer_callback(callback)  # неизвестное действие — молча


def _keyboard(kind: str, result: DailyPostResult, lang: str) -> InlineKeyboardMarkup | None:
    """Кнопка дня под постом; без записи в `daily_posts` кнопки нет."""
    if result.post_id is None:  # запись не создалась — показываем пост без кнопки
        return None
    if kind == KIND_WORDS:
        return daily_words_keyboard(result.post_id, lang)
    return daily_grammar_keyboard(result.post_id, lang)


def build_router() -> Router:
    """Собирает роутер слов и грамматики дня."""
    router = Router(name="daily")

    router.message.filter(F.chat.type.in_(GROUP_CHAT_TYPES))
    router.callback_query.filter(F.message.chat.type.in_(GROUP_CHAT_TYPES))

    router.message.register(word_command, Command("word"))
    router.message.register(grammar_command, Command("grammar"))
    router.callback_query.register(daily_button, F.data.startswith(DAILY_PREFIX))
    return router
