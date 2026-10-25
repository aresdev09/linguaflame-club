"""Тест миграций: upgrade, downgrade и снова upgrade на чистой базе."""

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect

EXPECTED_TABLES = {
    "ai_usage",
    "chat_content_history",
    "chats",
    "content_cache",
    "daily_posts",
    "game_answers",
    "game_sessions",
    "group_streaks",
    "members",
    "question_bank",
    "scheduled_messages",
    "score_events",
}


def _alembic_config(database_url: str) -> AlembicConfig:
    """Конфиг Alembic для теста: адрес базы задаётся атрибутом env.py."""
    config = AlembicConfig("alembic.ini")
    config.attributes["database_url"] = database_url
    config.attributes["skip_logging_config"] = True
    return config


def _tables(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    return names


def test_upgrade_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "migration_test.db"
    config = _alembic_config(f"sqlite+aiosqlite:///{db_path.as_posix()}")

    command.upgrade(config, "head")
    tables_after_upgrade = _tables(db_path)
    assert EXPECTED_TABLES <= tables_after_upgrade
    assert "alembic_version" in tables_after_upgrade

    command.downgrade(config, "base")
    assert _tables(db_path) <= {"alembic_version"}

    command.upgrade(config, "head")
    assert _tables(db_path) == tables_after_upgrade
