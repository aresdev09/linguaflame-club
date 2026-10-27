"""ORM-модели базы данных.

Все даты и время хранятся в UTC (в PostgreSQL — `timestamptz`).
"""

import uuid
from datetime import UTC, datetime, time
from datetime import date as date_type

from sqlalchemy import (
    JSON,
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
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Текущее время в UTC."""
    return datetime.now(UTC)


# BIGINT в PostgreSQL, INTEGER в SQLite (нужен для автоинкремента primary key).
BigIntType = BigInteger().with_variant(Integer, "sqlite")

# Источники начислений в журнале очков.
# `reset` — не начисление, а отмена: отрицательное событие, которым
# сбрасывается рейтинг. Отдельная запись нужна, чтобы сброс можно было
# откатить, не удаляя исходные начисления.
score_source_enum = Enum(
    "quiz",
    "scramble",
    "gap",
    "race",
    "truefalse",
    "daily_bonus",
    "streak_bonus",
    "reset",
    name="score_source",
    native_enum=False,
)

# Действия администраторов и участников, которые стоит сохранить в журнал.
admin_action_enum = Enum(
    "stop_game",
    "reset_scores",
    "undo_reset",
    "opt_out",
    "opt_in",
    name="admin_action",
    native_enum=False,
)

# Виды игр и статусы сессий.
game_kind_enum = Enum(
    "quiz", "scramble", "gap", "race", "truefalse", name="game_kind", native_enum=False
)
game_status_enum = Enum(
    "running", "finished", "stopped", "expired", name="game_status", native_enum=False
)

# Виды дневных постов и контента (общий кеш, банк вопросов, история).
daily_post_kind_enum = Enum("words", "grammar", "recap", name="daily_post_kind", native_enum=False)

# Виды автосообщений планировщика: по ним считается лимит на сутки.
scheduled_message_kind_enum = Enum(
    "daily",
    "reminder",
    "streak_lost",
    "milestone",
    "recap",
    name="scheduled_message_kind",
    native_enum=False,
)
content_kind_enum = Enum(
    "words",
    "grammar",
    "quiz_batch",
    "scramble",
    "gap",
    "race",
    "truefalse",
    name="content_kind",
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
    translation_language: Mapped[str] = mapped_column(String(8), default="en", server_default="en")
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
    chat_id: Mapped[int] = mapped_column(BigIntType, ForeignKey("chats.id", onupdate="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigIntType)
    points: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(score_source_enum)
    # Сессия игры, за которую начислены очки; None — очки вне игры.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("game_sessions.id")
    )
    # Сброс рейтинга, к которому относится отмена (см. `ScoreReset`).
    # Не внешний ключ: строки сброса не удаляются, только помечаются отменёнными.
    reset_id: Mapped[int | None] = mapped_column(BigIntType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_score_events_chat_id_created_at", "chat_id", "created_at"),
        Index("ix_score_events_chat_id_user_id", "chat_id", "user_id"),
        Index("ix_score_events_reset_id", "reset_id"),
    )


class ScoreReset(Base):
    """Сброс рейтинга группы: запись нужна для отката в течение 24 часов.

    Очки не удаляются: каждому участнику пишется отрицательное событие
    `score_events` с источником `reset`, привязанное к этой записи. Пока
    `undone_at` пуст, сброс действует; откат удаляет только события отмены.
    """

    __tablename__ = "score_resets"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigIntType, ForeignKey("chats.id", onupdate="CASCADE"))
    # Кто из администраторов выполнил сброс.
    admin_user_id: Mapped[int] = mapped_column(BigIntType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Заполняется при откате сброса.
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_score_resets_chat_id_created_at", "chat_id", "created_at"),)


class AdminAction(Base):
    """Журнал действий администраторов: кто остановил игру или сбросил рейтинг.

    Также хранит запросы участников об исключении из рейтинга (`/privacy`).
    """

    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigIntType, ForeignKey("chats.id", onupdate="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigIntType)
    action: Mapped[str] = mapped_column(admin_action_enum)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_admin_actions_chat_id_created_at", "chat_id", "created_at"),)


class GroupStreak(Base):
    """Общий огонёк группы: серия дней с активностью."""

    __tablename__ = "group_streaks"

    chat_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("chats.id", onupdate="CASCADE"), primary_key=True
    )
    current_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    best_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_active_date: Mapped[date_type | None] = mapped_column(Date)


class GameSession(Base):
    """Сессия групповой игры: квиз или мини-игра.

    Одна активная сессия на чат обеспечивается частичным уникальным
    индексом по `chat_id` для статуса `running`.
    """

    __tablename__ = "game_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[int] = mapped_column(BigIntType, ForeignKey("chats.id", onupdate="CASCADE"))
    kind: Mapped[str] = mapped_column(game_kind_enum)
    status: Mapped[str] = mapped_column(
        game_status_enum, default="running", server_default="running"
    )
    # Раунды, текущий раунд и контент сессии.
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    message_id: Mapped[int | None] = mapped_column(BigIntType)
    started_by: Mapped[int] = mapped_column(BigIntType)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Источник правды для таймеров: раунд закрывается по серверному времени.
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_game_sessions_chat_id_status", "chat_id", "status"),
        # Частичный уникальный индекс: не больше одной игры в статусе
        # `running` на чат. Завершённые и остановленные сессии не мешают.
        Index(
            "uq_game_sessions_chat_id_running",
            "chat_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )


class GameAnswer(Base):
    """Ответ участника на раунд игры."""

    __tablename__ = "game_answers"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("game_sessions.id")
    )
    round: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(BigIntType)
    answer: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean)
    answer_time_ms: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Один ответ на участника в раунде.
        Index(
            "uq_game_answers_session_id_round_user_id",
            "session_id",
            "round",
            "user_id",
            unique=True,
        ),
    )


class DailyPost(Base):
    """Пост дня (слова, грамматика, итоги недели).

    Запись создаётся до отправки: `posted_at` пустой означает «отправляется»,
    заполненный — «отправлено». Уникальный индекс защищает от дублей.
    """

    __tablename__ = "daily_posts"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigIntType, ForeignKey("chats.id", onupdate="CASCADE"))
    date: Mapped[date_type] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(daily_post_kind_enum)
    payload: Mapped[dict | None] = mapped_column(JSON)
    message_id: Mapped[int | None] = mapped_column(BigIntType)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Защита от дублей: один пост каждого вида в день на чат.
        Index("uq_daily_posts_chat_id_date_kind", "chat_id", "date", "kind", unique=True),
        Index("ix_daily_posts_chat_id_date", "chat_id", "date"),
    )


class ScheduledMessage(Base):
    """Журнал автосообщений планировщика: защита от дублей и лимит в сутки.

    Запись создаётся до отправки: пустой `posted_at` — сообщение ещё не
    ушло. `attempts` считает неудачные попытки (в том числе генерацию
    контента), `next_attempt_at` переносит повтор на более позднее время.
    """

    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigIntType, ForeignKey("chats.id", onupdate="CASCADE"))
    # Дата в часовом поясе чата: лимит сообщений считается по дню группы.
    date: Mapped[date_type] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(scheduled_message_kind_enum)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[int | None] = mapped_column(BigIntType)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Один вид автосообщения в день на чат: повторный tick дубль не создаст.
        Index(
            "uq_scheduled_messages_chat_id_date_kind",
            "chat_id",
            "date",
            "kind",
            unique=True,
        ),
        # Подсчёт сообщений дня для лимита в сутки.
        Index("ix_scheduled_messages_chat_id_date", "chat_id", "date"),
    )


class ContentCache(Base):
    """Общий кеш контента: одинаковый для групп одного уровня и языка."""

    __tablename__ = "content_cache"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    kind: Mapped[str] = mapped_column(content_kind_enum)
    level: Mapped[str] = mapped_column(String(2))
    language: Mapped[str] = mapped_column(String(8))
    # Дневной контент (слова, грамматика) привязан к дате; None — без даты.
    date: Mapped[date_type | None] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Поиск готового контента по виду, уровню, языку и дате.
        Index(
            "ix_content_cache_kind_level_language_date",
            "kind",
            "level",
            "language",
            "date",
        ),
    )


class QuestionBankItem(Base):
    """Запасной банк вопросов; пополняется результатами генерации."""

    __tablename__ = "question_bank"

    id: Mapped[int] = mapped_column(BigIntType, primary_key=True)
    level: Mapped[str] = mapped_column(String(2))
    language: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(content_kind_enum)
    payload: Mapped[dict] = mapped_column(JSON)
    times_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Выбор вопросов из банка, начиная с реже всего использованных.
        Index("ix_question_bank_level_language_kind", "level", "language", "kind"),
    )


class ChatContentHistory(Base):
    """Контент, уже использованный в группе: не повторяем его 30 дней."""

    __tablename__ = "chat_content_history"

    chat_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("chats.id", onupdate="CASCADE"), primary_key=True
    )
    # Хеш содержимого (sha256 в hex): одинаковый контент — одинаковый хеш.
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(content_kind_enum)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Чистка старой истории по чату.
        Index("ix_chat_content_history_chat_id_used_at", "chat_id", "used_at"),
    )


class AIUsage(Base):
    """Дневной счётчик запросов к ИИ на чат."""

    __tablename__ = "ai_usage"

    chat_id: Mapped[int] = mapped_column(
        BigIntType, ForeignKey("chats.id", onupdate="CASCADE"), primary_key=True
    )
    # Дата в часовом поясе чата: квота сбрасывается в полночь группы.
    date: Mapped[date_type] = mapped_column(Date, primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
