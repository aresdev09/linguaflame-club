"""Тесты конфигурации: чтение окружения и понятные ошибки при запуске."""

import pytest

from bot.config import ConfigError, load_settings


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
