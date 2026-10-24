"""Рейтинг группы: `/top` с переключателем периода.

Роутер собирается фабрикой `build_router()`: обработчики остаются
обычными функциями, их удобно вызывать в тестах напрямую. Участники
с `opted_out` скрыты в самом запросе рейтинга.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.keyboards import top_period_keyboard
from bot.models import Chat
from bot.sender import Sender
from bot.services.scoring import (
    PERIOD_ALL,
    PERIOD_WEEK,
    ScoringService,
    format_top,
)

# Команды и кнопки этого роутера работают только в группах.
GROUP_CHAT_TYPES = {"group", "supergroup"}


async def show_top(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    chat: Chat,
    lang: str,
    sender: Sender,
    settings: Settings,
) -> None:
    """Показывает недельный рейтинг группы."""
    entries = await ScoringService(session_factory, settings).top(chat, PERIOD_WEEK)
    await sender.reply(
        message,
        format_top(entries, lang, PERIOD_WEEK),
        reply_markup=top_period_keyboard(lang, PERIOD_WEEK),
    )


async def switch_top(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    chat: Chat,
    lang: str,
    sender: Sender,
    settings: Settings,
) -> None:
    """Переключает период рейтинга по кнопке «This week / All time»."""
    period = PERIOD_ALL if (callback.data or "").endswith(":all") else PERIOD_WEEK
    entries = await ScoringService(session_factory, settings).top(chat, period)
    if callback.message is not None:
        await sender.edit(
            callback.message.chat.id,
            callback.message.message_id,
            format_top(entries, lang, period),
            reply_markup=top_period_keyboard(lang, period),
        )
    await sender.answer_callback(callback)


def build_router() -> Router:
    """Собирает роутер рейтинга."""
    router = Router(name="leaderboard")

    router.message.filter(F.chat.type.in_(GROUP_CHAT_TYPES))
    router.callback_query.filter(F.message.chat.type.in_(GROUP_CHAT_TYPES))

    router.message.register(show_top, Command("top"))
    router.callback_query.register(switch_top, F.data.startswith("top:"))
    return router
