"""Админ-команды и приватность: `/stop`, `/reset_scores`, `/privacy`.

Права проверяются тем же кешем, что и настройки: анонимные админы тоже
считаются админами. Сброс рейтинга выполняется только по кнопке, а
запись отмены позволяет вернуть рейтинг в течение 24 часов. Обычные
участники не могут ни остановить игру, ни сбросить рейтинг; отказ
показывается без спама — не чаще раза в минуту.
"""

from time import monotonic

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.admins import GROUP_ANONYMOUS_BOT_ID, AdminCache, check_admin
from bot.config import Settings
from bot.keyboards import (
    privacy_keyboard,
    reset_confirm_keyboard,
    reset_undo_keyboard,
)
from bot.locales import t
from bot.models import Chat
from bot.repositories import ChatRepo, MemberRepo
from bot.sender import Sender
from bot.services.admin_actions import (
    RESET_TOO_OLD,
    AdminActionsService,
)
from bot.services.games.manager import GameManager

# Команды и кнопки этого роутера работают только в группах.
GROUP_CHAT_TYPES = {"group", "supergroup"}

# Отказ не-админу показываем не чаще раза в минуту на пользователя.
_REFUSAL_INTERVAL_SECONDS = 60.0
_last_refusal: dict[tuple[int, int], float] = {}


def _refusal_allowed(chat_id: int, user_id: int) -> bool:
    """True, если отказ можно показать: без спама раз в минуту."""
    now = monotonic()
    key = (chat_id, user_id)
    previous = _last_refusal.get(key)
    if previous is not None and now - previous < _REFUSAL_INTERVAL_SECONDS:
        return False
    _last_refusal[key] = now
    return True


def _actor_id(event: Message | CallbackQuery) -> int:
    """Id того, кто выполнил действие; анонимный админ — служебный аккаунт."""
    user = event.from_user
    return user.id if user is not None else 0


async def stop_command(
    message: Message,
    chat: Chat,
    lang: str,
    sender: Sender,
    admins: AdminCache,
    games: GameManager,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Останавливает активную игру. Очки закрытых раундов сохраняются."""
    if not await check_admin(message, admins):
        if _refusal_allowed(chat.id, _actor_id(message)):
            await sender.reply(message, t("common.not_admin", lang))
        return

    stopped = await games.stop(chat.id)
    if not stopped:
        await sender.reply(message, t("admin.stop_no_game", lang))
        return
    await AdminActionsService(session_factory).log(chat.id, _actor_id(message), "stop_game")
    await sender.reply(message, t("game.stopped", lang))


async def reset_scores_command(
    message: Message,
    chat: Chat,
    lang: str,
    sender: Sender,
    admins: AdminCache,
) -> None:
    """Спрашивает подтверждение перед сбросом рейтинга группы."""
    if not await check_admin(message, admins):
        if _refusal_allowed(chat.id, _actor_id(message)):
            await sender.reply(message, t("common.not_admin", lang))
        return
    await sender.reply(
        message,
        t("admin.reset_confirm", lang),
        reply_markup=reset_confirm_keyboard(lang),
    )


async def reset_callback(
    callback: CallbackQuery,
    chat: Chat,
    lang: str,
    sender: Sender,
    admins: AdminCache,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Кнопки сброса: подтвердить, отменить или вернуть рейтинг."""
    if not await check_admin(callback, admins):
        user_id = _actor_id(callback)
        text = t("common.not_admin", lang) if _refusal_allowed(chat.id, user_id) else None
        await sender.answer_callback(callback, text)
        return
    if callback.message is None:
        await sender.answer_callback(callback)
        return

    action = (callback.data or "").split(":", 1)[-1]
    service = AdminActionsService(session_factory, undo_hours=settings.score_reset_undo_hours)
    message_id = callback.message.message_id

    if action == "confirm":
        outcome = await service.reset_scores(chat.id, _actor_id(callback))
        if outcome.ok:
            await sender.edit(
                chat.id,
                message_id,
                t(
                    "admin.reset_done",
                    lang,
                    users=outcome.users,
                    points=outcome.points,
                ),
                reply_markup=reset_undo_keyboard(lang),
            )
        else:
            await sender.edit(chat.id, message_id, t("admin.reset_nothing", lang))
    elif action == "cancel":
        await sender.edit(chat.id, message_id, t("admin.reset_cancelled", lang))
    elif action == "undo":
        outcome = await service.undo_reset(chat.id, _actor_id(callback))
        if outcome.ok:
            text = t("admin.reset_undone", lang, points=outcome.points)
        elif outcome.result == RESET_TOO_OLD:
            text = t("admin.reset_too_old", lang)
        else:
            text = t("admin.reset_none", lang)
        await sender.edit(chat.id, message_id, text)
    await sender.answer_callback(callback)


def _privacy_text(lang: str) -> str:
    return f"{t('privacy.title', lang)}\n\n{t('privacy.body', lang)}"


async def privacy_command(
    message: Message,
    chat: Chat,
    lang: str,
    sender: Sender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показывает, что хранит бот, и даёт скрыться из рейтинга."""
    user = message.from_user
    if user is None or user.id == GROUP_ANONYMOUS_BOT_ID:
        await sender.reply(message, _privacy_text(lang))
        return
    async with session_factory() as session:
        chat_row = await ChatRepo(session).get(chat.id)
        member = await MemberRepo(session).get(chat.id, user.id)
    opted_out = bool(member.opted_out) if member is not None else False
    if chat_row is None:
        return
    await sender.reply(message, _privacy_text(lang), reply_markup=privacy_keyboard(lang, opted_out))


async def privacy_callback(
    callback: CallbackQuery,
    chat: Chat,
    lang: str,
    sender: Sender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Скрывает участника из рейтинга или возвращает его обратно."""
    user = callback.from_user
    if user is None or callback.message is None:
        await sender.answer_callback(callback)
        return

    opted_out = (callback.data or "").endswith(":hide")
    async with session_factory() as session:
        async with session.begin():
            repo = MemberRepo(session)
            await repo.get_or_create(
                chat.id,
                user.id,
                display_name=user.full_name,
                username=user.username,
            )
            await repo.set_opted_out(chat.id, user.id, opted_out)

    await AdminActionsService(session_factory).log(
        chat.id, user.id, "opt_out" if opted_out else "opt_in"
    )
    await sender.answer_callback(
        callback, t("privacy.hidden" if opted_out else "privacy.shown", lang)
    )
    await sender.edit(
        chat.id,
        callback.message.message_id,
        _privacy_text(lang),
        reply_markup=privacy_keyboard(lang, opted_out),
    )


def build_router() -> Router:
    """Собирает роутер админ-команд и приватности."""
    router = Router(name="admin")

    router.message.filter(F.chat.type.in_(GROUP_CHAT_TYPES))
    router.callback_query.filter(F.message.chat.type.in_(GROUP_CHAT_TYPES))

    router.message.register(stop_command, Command("stop"))
    router.message.register(reset_scores_command, Command("reset_scores"))
    router.message.register(privacy_command, Command("privacy"))
    router.callback_query.register(reset_callback, F.data.startswith("reset:"))
    router.callback_query.register(privacy_callback, F.data.startswith("privacy:"))
    return router
