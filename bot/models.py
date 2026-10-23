"""ORM-модели базы данных.

Все даты и время хранятся в UTC (в PostgreSQL — `timestamptz`).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""
