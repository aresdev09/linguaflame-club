"""Единая настройка логирования проекта.

Уровни разделены по смыслу: `DEBUG` — диагностика (разброс автосообщений,
повторные попытки), `INFO` — ход работы (старт, отправленные посты),
`WARNING` — восстановимые сбои (чат недоступен, база занята), `ERROR` —
сбои, которые нужно разбирать.

Формат строки выбирается переменной `LOG_JSON`: по умолчанию — читаемый
текст, при `true` — структурный JSON (одно событие на строку), удобный
для сборщиков логов на сервере.
"""

import json
import logging
from datetime import UTC, datetime

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Библиотеки, чьи INFO-сообщения слишком шумные для постоянной работы.
NOISY_LOGGERS: tuple[str, ...] = ("httpx", "httpcore", "aiogram.event")

# Поля, которые всегда есть в `LogRecord`: их не дублируем как «дополнительные».
_BASE_FIELDS = frozenset(vars(logging.LogRecord("", logging.NOTSET, "", 0, "", (), None)))
_SKIP_FIELDS = _BASE_FIELDS | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Структурный лог: одна JSON-строка на событие.

    В строку попадают время (UTC), уровень, имя логгера, сообщение,
    текст исключения и любые дополнительные поля из `extra`.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        for key, value in record.__dict__.items():
            if key not in _SKIP_FIELDS:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    """Настраивает корневой логгер: уровень, формат, тихие библиотеки."""
    numeric_level = getattr(logging, str(level).upper(), None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logging.basicConfig(level=numeric_level, handlers=[handler], force=True)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
