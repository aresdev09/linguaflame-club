"""Единая настройка логирования проекта.

Формат строки: время | уровень | модуль | сообщение.
"""

import logging

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Библиотеки, чьи INFO-сообщения слишком шумные для постоянной работы.
NOISY_LOGGERS: tuple[str, ...] = ("httpx", "httpcore", "aiogram.event")


def setup_logging(level: str = "INFO") -> None:
    """Настраивает корневой логгер: уровень, формат, тихие библиотеки."""
    numeric_level = getattr(logging, str(level).upper(), None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logging.basicConfig(level=numeric_level, handlers=[handler], force=True)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
