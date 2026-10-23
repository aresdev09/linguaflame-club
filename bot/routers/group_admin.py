"""Настройка группы: события бота в чате, /setup и /settings.

Роутер собирается фабрикой `build_router()`: диспетчер получает свежий
экземпляр, а обработчики остаются обычными функциями — их удобно вызывать
в тестах напрямую.
"""

import logging
from time import monotonic

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import AdminCache, check_admin
from bot.keyboards import settings_menu
from bot.locales import t
from bot.models import Chat
from bot.repositories import ChatRepo
from bot.sender import Sender
from bot.services.group_lifecycle import (
    migrate_chat,
    register_bot_added,
    register_bot_removed,
)
from bot.services.settings import (
    apply_callback_setting,
    apply_manual_setting,
    settings_summary,
)

logger = logging.getLogger(__name__)

# Команды и события этого роутера работают только в группах.
GROUP_CHAT_TYPES = {"group", "supergroup"}

# Статусы бота, при которых он «в чате».
MEMBER_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
# Статусы, из которых возвращение в чат считается добавлением.
OUTSIDE_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}

# Отказ не-админу показываем не чаще раза в минуту на пользователя.
_REFUSAL_INTERVAL_SECONDS = 60.0
_last_refusal: dict[tuple[int, int], float] = {}


class SetupInput(StatesGroup):
    """Админ вводит настройку ответом на сообщение бота."""

    timezone = State()
    daily_time = State()


def _refusal_allowed(chat_id: int, user_id: int) -> bool:
    """True, если отказ можно показать: без спама раз в минуту."""
    now = monotonic()
    key = (chat_id, user_id)
    previous = _last_refusal.get(key)
    if previous is not None and now - previous < _REFUSAL_INTERVAL_SECONDS:
        return False
    _last_refusal[key] = now
    return True


async def bot_membership_changed(
    update: ChatMemberUpdated,
    session_factory: async_sessionmaker[AsyncSession],
    sender: Sender,
) -> None:
    """Бот добавлен, удалён или снова добавлен в группу."""
    chat = update.chat
    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status

    if new_status in OUTSIDE_STATUSES:
        await register_bot_removed(session_factory, chat_id=chat.id)
        return

    was_outside = old_status in OUTSIDE_STATUSES
    if new_status in MEMBER_STATUSES and was_outside:
        chat_row = await register_bot_added(
            session_factory,
            chat_id=chat.id,
            title=chat.title,
            chat_type=chat.type,
        )
        await sender.send(chat.id, t("welcome.added", chat_row.ui_language))


async def on_group_upgraded(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Группа стала супергруппой: переносим данные на новый chat_id."""
    new_chat_id = message.migrate_to_chat_id
    if new_chat_id is None:  # защита от служебного сообщения без миграции
        return
    await migrate_chat(
        session_factory, old_chat_id=message.chat.id, new_chat_id=new_chat_id
    )


async def on_group_upgrade_seen_in_supergroup(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Второе служебное сообщение о миграции: приходит уже в супергруппе.

    Обработчик идемпотентен: если перенос уже сделан первым сообщением,
    повторный вызов ничего не меняет.
    """
    old_chat_id = message.migrate_from_chat_id
    if old_chat_id is None:
        return
    await migrate_chat(
        session_factory, old_chat_id=old_chat_id, new_chat_id=message.chat.id
    )


async def open_settings(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    chat: Chat,
    lang: str,
    admins: AdminCache,
    sender: Sender,
    state: FSMContext,
) -> None:
    """Открывает меню настроек группы: только для администраторов."""
    await state.clear()  # незавершённый ручной ввод отменяется

    if not await check_admin(message, admins):
        user_id = message.from_user.id if message.from_user is not None else 0
        if _refusal_allowed(message.chat.id, user_id):
            await sender.reply(message, t("common.not_admin", lang))
        return

    # Запоминаем тему форума, где открыли настройки: туда пойдут посты дня.
    if message.message_thread_id is not None:
        async with session_factory() as session:
            async with session.begin():
                await ChatRepo(session).update_settings(
                    chat.id, thread_id=message.message_thread_id
                )

    await sender.reply(
        message, settings_summary(chat, lang), reply_markup=settings_menu(chat, lang)
    )


async def change_setting(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    chat: Chat,
    lang: str,
    admins: AdminCache,
    sender: Sender,
    state: FSMContext,
) -> None:
    """Меняет настройку по кнопке: права нажавшего проверяются заново."""
    if not await check_admin(callback, admins):
        user_id = callback.from_user.id if callback.from_user is not None else 0
        text = t("common.not_admin", lang) if _refusal_allowed(chat.id, user_id) else None
        await sender.answer_callback(callback, text)
        return

    data = callback.data or ""
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    if action == "manual":
        field = parts[2] if len(parts) > 2 else ""
        await _start_manual_input(callback, field, lang, sender, state)
        return

    await apply_callback_setting(session_factory, chat.id, data)
    await _refresh_menu(callback, session_factory, chat.id, sender)
    await sender.answer_callback(callback)


async def _start_manual_input(
    callback: CallbackQuery,
    field: str,
    lang: str,
    sender: Sender,
    state: FSMContext,
) -> None:
    """Просит админа ввести значение ответом на сообщение бота."""
    if field not in ("timezone", "time") or callback.message is None:
        await sender.answer_callback(callback)
        return
    prompt_key = "setup.timezone_prompt" if field == "timezone" else "setup.time_prompt"
    prompt = await sender.send(
        callback.message.chat.id,
        t(prompt_key, lang),
        thread_id=callback.message.message_thread_id,
        reply_to_message_id=callback.message.message_id,
    )
    state_name = SetupInput.timezone if field == "timezone" else SetupInput.daily_time
    await state.set_state(state_name)
    await state.update_data(
        field=field,
        prompt_id=prompt.message_id if prompt is not None else None,
        menu_id=callback.message.message_id,
    )
    await sender.answer_callback(callback)


async def _refresh_menu(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    sender: Sender,
) -> None:
    """Перерисовывает меню после изменения настройки — на свежих данных."""
    if callback.message is None:
        return
    async with session_factory() as session:
        updated = await ChatRepo(session).get(chat_id)
    if updated is None:
        return
    await sender.edit(
        chat_id,
        callback.message.message_id,
        settings_summary(updated, updated.ui_language),
        reply_markup=settings_menu(updated, updated.ui_language),
    )


async def manual_timezone_input(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    chat: Chat,
    lang: str,
    admins: AdminCache,
    sender: Sender,
    state: FSMContext,
) -> None:
    """Ручной ввод часового пояса: ответ на приглашение бота."""
    await _handle_manual_input(
        message, "timezone", session_factory, chat, admins, lang, sender, state
    )


async def manual_time_input(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    chat: Chat,
    lang: str,
    admins: AdminCache,
    sender: Sender,
    state: FSMContext,
) -> None:
    """Ручной ввод времени поста: ответ на приглашение бота."""
    await _handle_manual_input(
        message, "time", session_factory, chat, admins, lang, sender, state
    )


async def _handle_manual_input(
    message: Message,
    field: str,
    session_factory: async_sessionmaker[AsyncSession],
    chat: Chat,
    admins: AdminCache,
    lang: str,
    sender: Sender,
    state: FSMContext,
) -> None:
    """Принимает значение из ответа: только на сообщение-приглашение."""
    data = await state.get_data()
    reply = message.reply_to_message
    if reply is None or reply.message_id != data.get("prompt_id"):
        return  # отвечают не на приглашение — состояние не трогаем

    if not await check_admin(message, admins):
        await state.clear()
        return

    error_key = await apply_manual_setting(
        session_factory, chat.id, field, message.text or ""
    )
    await state.clear()
    if error_key is not None:
        await sender.reply(message, t(error_key, lang))
        return
    await sender.reply(message, t("setup.saved", lang))

    # Обновляем открытое меню, чтобы в нём было новое значение.
    menu_id = data.get("menu_id")
    if isinstance(menu_id, int):
        async with session_factory() as session:
            updated = await ChatRepo(session).get(chat.id)
        if updated is not None:
            await sender.edit(
                chat.id,
                menu_id,
                settings_summary(updated, updated.ui_language),
                reply_markup=settings_menu(updated, updated.ui_language),
            )


def build_router() -> Router:
    """Собирает роутер: порядок обработчиков важен.

    Команды идут раньше ручного ввода, чтобы /setup во время ожидания
    ответа открывал меню, а не считался вводом значения.
    """
    router = Router(name="group_admin")

    router.message.filter(F.chat.type.in_(GROUP_CHAT_TYPES))
    router.my_chat_member.filter(F.chat.type.in_(GROUP_CHAT_TYPES))
    router.callback_query.filter(F.message.chat.type.in_(GROUP_CHAT_TYPES))

    router.my_chat_member.register(bot_membership_changed)
    router.message.register(on_group_upgraded, F.migrate_to_chat_id)
    router.message.register(on_group_upgrade_seen_in_supergroup, F.migrate_from_chat_id)
    router.message.register(open_settings, Command("setup", "settings"))
    router.callback_query.register(change_setting, F.data.startswith("set:"))
    router.message.register(
        manual_timezone_input,
        SetupInput.timezone,
        F.reply_to_message,
        F.text,
        ~F.text.startswith("/"),
    )
    router.message.register(
        manual_time_input,
        SetupInput.daily_time,
        F.reply_to_message,
        F.text,
        ~F.text.startswith("/"),
    )
    return router
