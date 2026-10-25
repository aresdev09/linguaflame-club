"""Конфигурация приложения.

Все настройки читаются из переменных окружения и файла `.env`
(шаблон — `.env.example`). Токены и ключи в коде не хранятся.
"""

from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./linguaflame-club.db"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# --- Варианты настроек группы (/setup) ---
# Уровни CEFR, которые задаёт админ группы.
LEVELS: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")
# Языки переводов и пояснений.
TRANSLATION_LANGUAGES: tuple[str, ...] = ("ru", "uz", "en", "tr", "es", "de")
# Языки интерфейса бота.
UI_LANGUAGES: tuple[str, ...] = ("en", "ru")
# Сколько слов публиковать в ежедневном посте.
WORDS_COUNT_OPTIONS: tuple[int, ...] = (5, 10, 20)
# Популярные часовые пояса кнопками; остальные — вводом вручную.
POPULAR_TIMEZONES: tuple[str, ...] = (
    "UTC",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Tashkent",
    "Asia/Samarkand",
    "Asia/Almaty",
)
# Время ежедневного поста кнопками; своё — вводом вручную.
POST_TIME_OPTIONS: tuple[str, ...] = ("08:00", "09:00", "19:00", "20:00")
# Тихие часы: пресеты; «выкл» — совпадающие начало и конец.
QUIET_HOURS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("22:00", "07:00"),
    ("23:00", "08:00"),
    ("21:00", "08:00"),
)


class ConfigError(RuntimeError):
    """Понятная ошибка конфигурации, показывается пользователю при запуске."""


class Settings(BaseSettings):
    """Настройки приложения из окружения и `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram ---
    bot_token: str = Field(min_length=1)
    # Имя бота без @ (для ссылки «Add to group»); можно задать позже.
    bot_username: str = ""

    # --- Инфраструктура ---
    # Продакшен — PostgreSQL, разработка — SQLite.
    database_url: str = DEFAULT_DATABASE_URL
    redis_url: str = DEFAULT_REDIS_URL
    log_level: str = "INFO"

    # --- ИИ (OpenAI-совместимый API; по умолчанию NVIDIA NIM) ---
    ai_server_url: str = "https://integrate.api.nvidia.com/v1"
    ai_api_key: str = ""
    ai_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    # Резервная модель, если основная не отвечает; пусто — работать без резерва.
    ai_model_fallback: str = "deepseek-ai/deepseek-v4.1-flash"
    # Таймаут одного запроса к ИИ, секунды. Холодный старт модели бывает долгим.
    ai_timeout_seconds: float = Field(default=90.0, gt=0)
    # Дневная квота запросов к ИИ на один чат (0 — без ограничения).
    ai_quota_per_day: int = Field(default=30, ge=0)
    # Глобальный дневной бюджет запросов к ИИ по всем чатам (0 — без ограничения).
    ai_global_daily_limit: int = Field(default=300, ge=0)

    # --- Очки за игры (менять без правки кода через окружение) ---
    score_quiz_correct: int = 10  # верный ответ в квизе
    score_quiz_speed_bonus_max: int = 5  # максимум бонуса за скорость
    score_quiz_first_bonus: int = 2  # бонус первому верному ответу в раунде
    score_scramble_correct: int = 5  # «Собери слово»
    score_gap_correct: int = 8  # «Пропущенное слово»
    score_race_word_max: int = 6  # «Гонка переводов», за слово
    score_truefalse_correct: int = 5  # «Правда или ложь»
    score_participation: int = 2  # участие в игре до конца сессии
    score_daily_bonus: int = 3  # кнопка «Got it» под постом дня
    score_streak_bonus: int = 2  # бонус активным в день общего огонька
    score_daily_cap: int = 300  # максимум очков на участника в день (0 — без лимита)
    # Сколько участников показывать в /top.
    leaderboard_top_limit: int = Field(default=10, ge=1, le=50)

    # --- Лимиты Telegram и антиспам ---
    limit_messages_per_minute: int = 20  # сообщений на один чат в минуту
    limit_global_per_second: float = 20.0  # глобальный лимит отправок в секунду
    limit_command_cooldown_seconds: float = 3.0  # пауза между командами
    limit_heavy_command_cooldown_seconds: float = 10.0  # квиз, /word и т.п.
    limit_game_cooldown_seconds: float = 10.0  # пауза между играми в чате
    limit_games_per_day: int = 30  # игр в день на один чат

    # --- Игры ---
    # Время на раунд квиза, секунды (настраивается 15–30).
    quiz_round_seconds: int = Field(default=20, ge=15, le=30)
    # Пауза между раундами квиза, секунды.
    quiz_break_seconds: int = Field(default=3, ge=1, le=15)
    # Ответы быстрее не получают бонус скорости (антиспам, миллисекунды).
    score_fast_answer_ms: int = Field(default=500, ge=0)


def _describe_settings_error(exc: ValidationError) -> str:
    """Собирает понятный текст ошибки по ошибкам валидации настроек."""
    problems = []
    for error in exc.errors():
        field = str(error["loc"][0]).upper() if error["loc"] else "?"
        if error["type"] == "missing":
            reason = "переменная не задана"
        elif error["type"] == "string_too_short":
            reason = "пустое значение"
        else:
            reason = error["msg"]
        problems.append(f"- {field}: {reason}")
    details = "\n".join(problems)
    return (
        "Не удалось загрузить настройки из окружения:\n"
        f"{details}\n"
        "Скопируйте .env.example в .env и заполните значения "
        "(или задайте переменные окружения)."
    )


def load_settings() -> Settings:
    """Читает настройки и при ошибке поднимает ConfigError с понятным текстом."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigError(_describe_settings_error(exc)) from exc


@lru_cache
def get_settings() -> Settings:
    """Кешированная версия load_settings для запуска приложения."""
    return load_settings()
