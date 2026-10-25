"""Тесты конфигурации: чтение окружения и понятные ошибки при запуске."""

from datetime import time, timedelta

import pytest

from bot.config import ConfigError, load_settings, parse_time_offset


def test_load_settings_reads_environment(monkeypatch, tmp_path):
    """Настройки читаются из переменных окружения."""
    monkeypatch.chdir(tmp_path)  # чтобы не подхватить локальный .env
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = load_settings()

    assert settings.bot_token == "123:TEST"
    assert settings.log_level == "DEBUG"


def test_missing_bot_token_raises_config_error(monkeypatch, tmp_path):
    """Без BOT_TOKEN приложение падает с понятным сообщением."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    with pytest.raises(ConfigError) as exc:
        load_settings()

    message = str(exc.value)
    assert "BOT_TOKEN" in message
    assert ".env.example" in message


def test_empty_bot_token_raises_config_error(monkeypatch, tmp_path):
    """Пустой BOT_TOKEN считается ошибкой, а не валидным значением."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "")

    with pytest.raises(ConfigError) as exc:
        load_settings()

    message = str(exc.value)
    assert "BOT_TOKEN" in message
    assert "пустое значение" in message


def test_defaults_are_applied(monkeypatch, tmp_path):
    """Без явных значений лимиты, очки и адреса берутся по умолчанию."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")

    settings = load_settings()

    assert settings.database_url.startswith("sqlite+aiosqlite")
    assert settings.redis_url.startswith("redis://")
    assert settings.ai_quota_per_day == 30
    assert settings.limit_messages_per_minute == 20
    assert settings.score_quiz_correct == 10
    assert settings.score_daily_cap == 300
    assert settings.leaderboard_top_limit == 10
    assert settings.scheduler_time_offset == timedelta(0)
    assert settings.scheduler_messages_per_day == 2
    assert settings.scheduler_dormant_days == 7


def test_parse_time_offset_understands_units():
    """Сдвиг времени планировщика: дни, часы, минуты и знак."""
    assert parse_time_offset("2h") == timedelta(hours=2)
    assert parse_time_offset("1d3h30m") == timedelta(days=1, hours=3, minutes=30)
    assert parse_time_offset("90") == timedelta(minutes=90)
    assert parse_time_offset("-45m") == -timedelta(minutes=45)
    assert parse_time_offset("0") == timedelta(0)


def test_parse_time_offset_rejects_garbage():
    with pytest.raises(ValueError):
        parse_time_offset("2 часа")


def test_scheduler_settings_are_parsed_from_environment(monkeypatch, tmp_path):
    """Сдвиг времени и время напоминаний читаются из окружения."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("SCHEDULER_TIME_OFFSET", "3h")
    monkeypatch.setenv("SCHEDULER_REMINDER_TIME", "21:30")

    settings = load_settings()

    assert settings.scheduler_time_offset == timedelta(hours=3)
    assert settings.scheduler_reminder_time == time(21, 30)
    assert settings.scheduler_streak_lost_time == time(9, 0)


def test_bad_scheduler_offset_raises_config_error(monkeypatch, tmp_path):
    """Некорректный сдвиг — понятная ошибка при запуске, а не тихий сбой."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("SCHEDULER_TIME_OFFSET", "2 часа")

    with pytest.raises(ConfigError) as exc:
        load_settings()

    assert "SCHEDULER_TIME_OFFSET" in str(exc.value)
