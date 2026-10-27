"""Личный чат и онбординг: `/start`, `/help` и вежливые отказы.

В личке бот только объясняет, как добавить его в группу. Команды игр и
админские команды требуют группового контекста, поэтому в личном чате
на них отвечает понятный отказ со ссылкой «Add to group» — вместо
тишины или ошибки.

`/help` работает и в личке, и в группе: список команд по ролям.
Роутер собирается фабрикой `build_router()`, обработчики остаются
обычными функциями — их удобно вызывать в тестах напрямую.
"""

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message, User

from bot.config import Settings
from bot.keyboards import add_to_group_keyboard
from bot.locales import DEFAULT_LANGUAGE, t
from bot.sender import Sender

# Личка и группы: фильтры на уровне обработчиков, роутер общий.
PRIVATE_CHAT_TYPES = {"private"}
GROUP_CHAT_TYPES = {"group", "supergroup"}

# Команды, которые в личке не выполняются: игры, рейтинг и настройки
# группы. Порядок не важен, важен состав — новые групповые команды
# стоит добавлять сюда, чтобы они не «молчали» в личке.
GROUP_ONLY_COMMANDS = (
    "quiz",
    "games",
    "scramble",
    "gap",
    "race",
    "truefalse",
    "word",
    "grammar",
    "top",
    "me",
    "streak",
    "privacy",
    "setup",
    "settings",
    "stop",
    "reset_scores",
)


def _normalize_username(username: str | None) -> str:
    """Имя бота без «@»; пустая строка, если имени нет."""
    return (username or "").strip().lstrip("@").strip()


async def bot_username(bot: Bot, settings: Settings) -> str:
    """Имя бота для ссылки «Add to group».

    Сначала берём значение из настроек (быстро, без сети), затем — у
    самого Telegram через кешированный `getMe`.
    """
    username = _normalize_username(settings.bot_username)
    if username:
        return username
    me = await bot.me()
    return _normalize_username(me.username if isinstance(me.username, str) else "")


def _private_language(user: User | None) -> str:
    """Язык личного чата: русский для ru-локали, иначе английский.

    Язык интерфейса группы берётся из настроек чата, а у лички настроек
    нет — ориентируемся на язык клиента пользователя.
    """
    code = (user.language_code or "").lower() if user is not None else ""
    return "ru" if code.startswith("ru") else DEFAULT_LANGUAGE


async def start_private(message: Message, sender: Sender, bot: Bot, settings: Settings) -> None:
    """`/start` в личке: что умеет бот и кнопка «Add to group»."""
    lang = _private_language(message.from_user)
    username = await bot_username(bot, settings)
    await sender.reply(
        message,
        t("onboarding.start", lang),
        reply_markup=add_to_group_keyboard(username, lang),
    )


async def help_private(message: Message, sender: Sender, bot: Bot, settings: Settings) -> None:
    """`/help` в личке: список команд по ролям и кнопка добавления."""
    lang = _private_language(message.from_user)
    username = await bot_username(bot, settings)
    await sender.reply(
        message,
        _help_text(lang),
        reply_markup=add_to_group_keyboard(username, lang),
    )


async def help_group(message: Message, lang: str, sender: Sender) -> None:
    """`/help` (и `/start`) в группе: короткая справка по ролям."""
    await sender.reply(message, _help_text(lang))


async def group_only_in_private(
    message: Message, sender: Sender, bot: Bot, settings: Settings
) -> None:
    """Игра или админская команда в личке: понятный отказ, не тишина."""
    lang = _private_language(message.from_user)
    username = await bot_username(bot, settings)
    await sender.reply(
        message,
        t("private.group_only", lang),
        reply_markup=add_to_group_keyboard(username, lang),
    )


def _help_text(lang: str) -> str:
    """Справка: заголовок и два списка — участнику и админу."""
    return f"{t('help.title', lang)}\n\n{t('help.everyone', lang)}\n\n{t('help.admins', lang)}"


def build_router() -> Router:
    """Собирает роутер личного чата и справки.

    Команды с фильтром типа чата: `/start` и `/help` различаются для
    лички и группы, а отказы перехватывают групповые команды только в
    личке — в группе их обрабатывают свои роутеры.
    """
    router = Router(name="private")

    router.message.register(start_private, Command("start"), F.chat.type.in_(PRIVATE_CHAT_TYPES))
    router.message.register(help_private, Command("help"), F.chat.type.in_(PRIVATE_CHAT_TYPES))
    router.message.register(
        help_group,
        Command("start", "help"),
        F.chat.type.in_(GROUP_CHAT_TYPES),
    )
    router.message.register(
        group_only_in_private,
        Command(*GROUP_ONLY_COMMANDS),
        F.chat.type.in_(PRIVATE_CHAT_TYPES),
    )
    return router
