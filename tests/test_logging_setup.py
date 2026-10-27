"""Тесты логирования: текстовый и структурный (JSON) формат."""

import json
import logging
import sys

from bot.logging_setup import DATE_FORMAT, LOG_FORMAT, JsonFormatter, setup_logging


def _record(
    msg: str = "hello",
    *,
    level: int = logging.INFO,
    exc_info: tuple | None = None,
    extra: dict | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord("bot.test", level, __file__, 10, msg, (), exc_info)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_text_formatter_keeps_readable_format() -> None:
    """Текстовый формат — время | уровень | модуль | сообщение."""
    formatted = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT).format(_record("бот запущен"))

    assert "INFO" in formatted
    assert "bot.test" in formatted
    assert formatted.endswith("бот запущен")


def test_json_formatter_emits_structured_fields() -> None:
    """JSON-формат отдаёт время, уровень, логгер и сообщение."""
    payload = json.loads(JsonFormatter().format(_record("пост дня")))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "bot.test"
    assert payload["message"] == "пост дня"
    assert payload["time"].endswith("Z") or "+00:00" in payload["time"]


def test_json_formatter_includes_extras_and_exception() -> None:
    """Дополнительные поля и исключение попадают в структурный лог."""
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("сбой", level=logging.ERROR, exc_info=sys.exc_info())
    record.chat_id = -100

    payload = json.loads(JsonFormatter().format(record))

    assert payload["chat_id"] == -100
    assert "ValueError: boom" in payload["exception"]


def test_setup_logging_json_uses_json_formatter() -> None:
    """`json_logs=True` ставит структурный форматтер на корневой логгер."""
    try:
        setup_logging("DEBUG", json_logs=True)
        handler = logging.getLogger().handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)
        assert logging.getLogger().level == logging.DEBUG
    finally:
        setup_logging("INFO")


def test_setup_logging_falls_back_to_info_for_unknown_level() -> None:
    """Некорректный уровень не роняет запуск — берём INFO."""
    try:
        setup_logging("не-уровень")
        assert logging.getLogger().level == logging.INFO
    finally:
        setup_logging("INFO")
