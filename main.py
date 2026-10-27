"""Точка входа для панелей хостинга: миграции и запуск бота.

Многие панели (Pterodactyl/Wispbyte и подобные) запускают один файл:
`python main.py`. Обычный запуск проекта — модулем (`python -m bot.main`), и
при нём миграции применяются отдельно. Этот файл делает то же самое, но сам
поднимает схему базы до последней версии, поэтому его можно указать как
«main file» в панели.

Пути считаются от расположения файла, а `.env` читается из корня проекта —
запуск работает независимо от текущего каталога.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

# Настройки из .env; переменные окружения панели имеют приоритет.
load_dotenv(ROOT / ".env")


def upgrade_database() -> None:
    """Применяет миграции Alembic до последней версии (идемпотентно)."""
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")


def main() -> None:
    """Поднимает схему базы и запускает бота."""
    upgrade_database()
    from bot.main import run

    run()


if __name__ == "__main__":
    main()
