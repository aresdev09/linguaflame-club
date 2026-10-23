"""ORM-модели базы данных.

Все даты и время хранятся в UTC (в PostgreSQL — `timestamptz`).
"""

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    Uuid,
    false,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Текущее время в UTC."""
    return datetime.now(UTC)


# BIGINT в PostgreSQL, INTEGER в SQLite (нужен для автоинкремента primary key).
BigIntType = BigInteger().with_variant(Integer, "sqlite")

# Источники начислений в журнале очков.
score_source_enum = Enum(
    "quiz",
    "scramble",
    "gap",
    "race",
    "truefalse",
    "daily_bonus",
    "streak_bonus",
    name="score_source",
    native_enum=False,
)


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""


class Chat(Base):
    """Telegram-чат (группа или супергруппа) и его настройки."""

    __tablename__ = "chats"

    # Telegram chat id — отрицательное число.
    id: Mapped[int] = mapped_column(BigIntType, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(16), default="group", server_default="group")
    # False, если бота удалили из группы; данные живут ещё 30 дней.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    ui_language: Mapped[str] = mapped_column(String(8), default="en", server_default="en")
    translation_language: Mapped[str] = mapped_column(
        String(8), default="en", server_default="en"
    )
    level: Mapped[str] = mapped_column(String(2), default="B1", server_default="B1")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    daily_time: Mapped[time] = mapped_column(Time, default=time(9, 0))
    daily_words: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    daily_grammar: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    words_count: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    quiet_from: Mapped[time] = mapped_column(Time, default=time(22, 0))
    quiet_to: Mapped[time] = mapped_column(Time, default=time(7, 0))
    # Тема форума, в которой запускали /setup; None — обычная группа.
    thread_id: Mapped[int | None] = mapped_column(BigIntType)
    ai_quota_per_day: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Member(Base):
    """Участник группы: создаётся при первом действии, полный список не собирается."""

    __tablename__ = "members"

    chat_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("chats.id", onupdate="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    username: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Скрылся из рейтинга по запросу (см. /privacy).
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class ScoreEvent(Base):
    """Журнал начислений очков — источник правды для рейтинга.

    Отдельных счётчиков очков в таблицах нет: рейтинг считается
    агрегатом по этому журналу.
    """

    __tablename__ = "score_events"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("chats.id", onupdate="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(BigIntType)
    points: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(score_source_enum)
    # Сессия игры, за которую начислены очки; None — очки вне игры.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("game_sessions.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_score_events_chat_id_created_at", "chat_id", "created_at"),
        Index("ix_score_events_chat_id_user_id", "chat_id", "user_id"),
    )


class GroupStreak(Base):
    """Общий огонёк группы: серия дней с активностью."""

    __tablename__ = "group_streaks"

    chat_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("chats.id", onupdate="CASCADE"), primary_key=True
    )
    current_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    best_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_active_date: Mapped[date | None] = mapped_column(Date)
