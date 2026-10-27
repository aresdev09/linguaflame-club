"""Стартовый банк вопросов: генерация файлов через ИИ и загрузка в базу.

Запуск из корня проекта (модулем, чтобы импортировался пакет bot):

    python -m scripts.seed_question_bank --generate   # один раз, с ключом ИИ
    python -m scripts.seed_question_bank --load       # по умолчанию

Файлы банка лежат в `data/question_bank/{kind}_{level}.json` и попадают
в git: бот работает без ИИ, а загрузка идемпотентна — повторы пропускаются
по хешу содержимого.
"""

import argparse
import asyncio
import json
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import LEVELS, Settings, load_settings
from bot.database import get_session_factory
from bot.logging_setup import setup_logging
from bot.repositories import ContentRepo
from bot.services.ai_client import AIClient, build_ai_client
from bot.services.ai_errors import AIError
from bot.services.ai_quality import content_hash, item_key
from bot.services.ai_safety import find_stop_word_in
from bot.services.ai_schemas import (
    GapItem,
    QuizQuestion,
    RaceItem,
    ScrambleItem,
    TrueFalseItem,
)

logger = logging.getLogger(__name__)

# Где лежат файлы банка (относительно корня проекта).
BANK_DIR = Path("data/question_bank")
# Сколько элементов каждого типа и уровня хотим в банке.
BANK_COUNT = 30
# Виды контента в банке и их схемы проверки элементов.
ITEM_SCHEMAS: dict[str, type[BaseModel]] = {
    "quiz_batch": QuizQuestion,
    "scramble": ScrambleItem,
    "gap": GapItem,
    "race": RaceItem,
    "truefalse": TrueFalseItem,
}


# --- генерация файлов ---


async def _request_items(
    client: AIClient, kind: str, level: str, language: str, count: int
) -> list[dict]:
    """Одна пачка элементов у ИИ, разложенная по payload'ам."""
    if kind == "quiz_batch":
        response = await client.generate_quiz_batch(level, language, count=count)
        return [item.model_dump() for item in response.questions]
    if kind == "scramble":
        response = await client.generate_scramble(level, language, count=count)
        return [item.model_dump() for item in response.items]
    if kind == "gap":
        response = await client.generate_gap(level, language, count=count)
        return [item.model_dump() for item in response.items]
    if kind == "race":
        response = await client.generate_race(level, language, count=count)
        return [item.model_dump() for item in response.items]
    if kind == "truefalse":
        response = await client.generate_truefalse(level, language, count=count)
        return [item.model_dump() for item in response.items]
    raise ValueError(f"неизвестный вид контента: {kind}")


async def _generate_items(
    client: AIClient, kind: str, level: str, language: str, count: int
) -> list[dict]:
    """Собирает нужное число уникальных безопасных элементов одного вида."""
    collected: list[dict] = []
    seen: set[str] = set()
    for attempt in (1, 2):
        if len(collected) >= count:
            break
        needed = count - len(collected)
        payloads = await _request_items(client, kind, level, language, needed + 5)
        for payload in payloads:
            key = item_key(kind, payload)
            if not key or key in seen:
                continue
            if find_stop_word_in(payload) is not None:
                continue
            seen.add(key)
            collected.append(payload)
        if attempt == 1 and len(collected) < count:
            logger.info("%s %s: собрано %d из %d, догенерирую", kind, level, len(collected), count)
    return collected[:count]


def _save_file(path: Path, *, kind: str, level: str, language: str, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": kind,
        "level": level,
        "language": language,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "items": items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def generate_bank(
    client: AIClient,
    *,
    language: str,
    count: int,
    bank_dir: Path,
    levels: Iterable[str] = LEVELS,
    kinds: Sequence[str] = tuple(ITEM_SCHEMAS),
) -> int:
    """Создаёт файлы банка через ИИ; возвращает число полных файлов."""
    complete = 0
    for level in levels:
        for kind in kinds:
            try:
                items = await _generate_items(client, kind, level, language, count)
            except AIError as exc:
                logger.error("%s %s: ИИ не ответил: %s", kind, level, exc)
                continue
            if not items:
                logger.error("%s %s: не удалось собрать элементы", kind, level)
                continue
            if len(items) < count:
                logger.warning("%s %s: собрано только %d из %d", kind, level, len(items), count)
            else:
                complete += 1
            _save_file(
                bank_dir / f"{kind}_{level}.json",
                kind=kind,
                level=level,
                language=language,
                items=items,
            )
    return complete


# --- загрузка в базу ---


def _read_bank_file(path: Path) -> tuple[str, str, str, list[dict]] | None:
    """Читает и проверяет файл банка; возвращает (kind, level, language, items)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.error("%s: не удалось прочитать файл", path)
        return None
    kind = str(data.get("kind", ""))
    level = str(data.get("level", ""))
    language = str(data.get("language", ""))
    if kind not in ITEM_SCHEMAS or not level or not language:
        logger.error("%s: некорректная шапка файла", path)
        return None
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        logger.error("%s: нет списка items", path)
        return None

    schema = ITEM_SCHEMAS[kind]
    valid: list[dict] = []
    for raw in raw_items:
        try:
            payload = schema.model_validate(raw).model_dump()
        except ValidationError as exc:
            logger.warning("%s: элемент не прошёл схему (%s)", path.name, exc.error_count())
            continue
        if find_stop_word_in(payload) is not None:
            logger.warning("%s: элемент со стоп-словом пропущен", path.name)
            continue
        valid.append(payload)
    return kind, level, language, valid


async def load_bank(
    session_factory: async_sessionmaker[AsyncSession],
    bank_dir: Path,
) -> int:
    """Загружает файлы банка в базу; повторный запуск дублей не создаёт."""
    added_total = 0
    for path in sorted(bank_dir.glob("*.json")):
        parsed = _read_bank_file(path)
        if parsed is None:
            continue
        kind, level, language, items = parsed
        async with session_factory() as session:
            async with session.begin():
                repo = ContentRepo(session)
                existing = await repo.pick_bank_items(kind, level, language, limit=10_000)
                known = {content_hash(kind, item.payload) for item in existing}
                added = 0
                for payload in items:
                    digest = content_hash(kind, payload)
                    if digest in known:
                        continue
                    known.add(digest)
                    await repo.add_bank_item(kind, level, language, payload)
                    added += 1
        logger.info("%s: добавлено %d из %d", path.name, added, len(items))
        added_total += added
    return added_total


# --- запуск из консоли ---


async def _run_generate(settings: Settings, language: str, count: int) -> None:
    client = build_ai_client(settings)
    try:
        files = await generate_bank(client, language=language, count=count, bank_dir=BANK_DIR)
    finally:
        await client.close()
    logger.info("Готово: полных файлов %d (язык %s)", files, language)


def main() -> None:
    """Точка входа скрипта: генерация и/или загрузка банка."""
    parser = argparse.ArgumentParser(
        description="Стартовый банк вопросов: генерация через ИИ и загрузка в базу"
    )
    parser.add_argument("--generate", action="store_true", help="сгенерировать файлы через ИИ")
    parser.add_argument("--load", action="store_true", help="загрузить файлы в базу")
    parser.add_argument("--language", default="ru", help="язык переводов (по умолчанию ru)")
    parser.add_argument("--count", type=int, default=BANK_COUNT, help="элементов на тип и уровень")
    args = parser.parse_args()
    setup_logging()

    if args.generate:
        settings = load_settings()
        asyncio.run(_run_generate(settings, args.language, args.count))
    if args.load or not args.generate:
        session_factory = get_session_factory()
        total = asyncio.run(load_bank(session_factory, BANK_DIR))
        logger.info("Загружено новых элементов: %d", total)


if __name__ == "__main__":
    main()
