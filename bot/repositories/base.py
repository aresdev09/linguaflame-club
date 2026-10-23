"""Общая основа репозиториев."""

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepo:
    """Репозиторий работает в одной сессии; commit делает вызывающий код."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
