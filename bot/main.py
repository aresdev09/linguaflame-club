"""Точка входа бота: запуск в режиме long polling."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import AdminCache
from bot.config import ConfigError, Settings, get_settings
from bot.database import get_session_factory
from bot.logging_setup import setup_logging
from bot.middlewares.errors import ErrorMiddleware
from bot.middlewares.group import GroupContextMiddleware
from bot.middlewares.throttle import ThrottleMiddleware
from bot.routers import games, group_admin, leaderboard, quiz
from bot.sender import Sender
from bot.services.ai_client import build_ai_client
from bot.services.content import ContentService
from bot.services.games.manager import GameManager
from bot.services.scoring import ScoringService
from bot.services.streak import StreakService

logger = logging.getLogger(__name__)

# Какие обновления обрабатываем: сообщения, кнопки и события бота в чате.
ALLOWED_UPDATES: list[str] = ["message", "callback_query", "my_chat_member"]


def build_dispatcher(
    session_factory: async_sessionmaker[AsyncSession],
    sender: Sender,
    settings: Settings,
) -> Dispatcher:
    """Собирает диспетчер: роутеры и middleware. FSM пока в памяти процесса."""
    dispatcher = Dispatcher()
    dispatcher.include_router(group_admin.build_router())
    dispatcher.include_router(quiz.build_router())
    dispatcher.include_router(games.build_router())
    dispatcher.include_router(leaderboard.build_router())

    # Ошибки ловим снаружи всех: они не должны ронять бота.
    dispatcher.update.outer_middleware(ErrorMiddleware(session_factory, sender))
    # Порядок важен: контекст группы нужен кулдаунам, которые идут следом.
    dispatcher.update.middleware(GroupContextMiddleware(session_factory))
    dispatcher.update.middleware(
        ThrottleMiddleware(
            user_cooldown=settings.limit_command_cooldown_seconds,
            heavy_cooldown=settings.limit_heavy_command_cooldown_seconds,
        )
    )
    return dispatcher


async def main(settings: Settings) -> None:
    """Запускает бота: проверяет токен и включает polling."""
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    session_factory = get_session_factory()
    sender = Sender(
        bot,
        per_chat_limit=settings.limit_messages_per_minute,
        global_limit=max(int(settings.limit_global_per_second), 1),
    )
    admins = AdminCache(bot)
    ai_client = build_ai_client(settings)
    content = ContentService(session_factory, ai_client, settings)
    scoring = ScoringService(session_factory, settings)
    streak = StreakService(session_factory)
    games = GameManager(
        session_factory=session_factory,
        settings=settings,
        sender=sender,
        content=content,
        scoring=scoring,
        streak=streak,
    )
    dispatcher = build_dispatcher(session_factory, sender, settings)

    me = await bot.get_me()
    logger.info("Бот @%s запускается в режиме long polling", me.username)

    try:
        # До первого апдейта: закрыть просроченные игры, возобновить живые.
        await games.recover()
        await dispatcher.start_polling(
            bot,
            allowed_updates=ALLOWED_UPDATES,
            session_factory=session_factory,
            sender=sender,
            admins=admins,
            settings=settings,
            games=games,
        )
    finally:
        await games.shutdown()
        await ai_client.close()
        await bot.session.close()
        logger.info("Бот остановлен")


def run() -> None:
    """Синхронная обёртка для запуска из консоли."""
    setup_logging()
    try:
        settings = get_settings()
        setup_logging(settings.log_level)
        asyncio.run(main(settings))
    except ConfigError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except TelegramUnauthorizedError:
        logger.error("Telegram отклонил BOT_TOKEN: проверьте токен в .env")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")


if __name__ == "__main__":
    run()
