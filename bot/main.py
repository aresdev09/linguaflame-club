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
from bot.middlewares.group import GroupContextMiddleware
from bot.middlewares.throttle import ThrottleMiddleware
from bot.routers import group_admin
from bot.sender import Sender

logger = logging.getLogger(__name__)

# Какие обновления обрабатываем: сообщения, кнопки и события бота в чате.
ALLOWED_UPDATES: list[str] = ["message", "callback_query", "my_chat_member"]


def build_dispatcher(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> Dispatcher:
    """Собирает диспетчер: роутеры и middleware. FSM пока в памяти процесса."""
    dispatcher = Dispatcher()
    dispatcher.include_router(group_admin.router)

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
    dispatcher = build_dispatcher(session_factory, settings)

    me = await bot.get_me()
    logger.info("Бот @%s запускается в режиме long polling", me.username)

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=ALLOWED_UPDATES,
            session_factory=session_factory,
            sender=sender,
            admins=admins,
        )
    finally:
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
