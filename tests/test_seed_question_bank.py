"""Тесты скрипта стартового банка вопросов."""

import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.repositories import ContentRepo
from bot.services.ai_errors import AIRequestError
from bot.services.ai_schemas import QuizBatch, QuizQuestion
from bot.services.fake_ai_client import FakeAIClient
from scripts.seed_question_bank import generate_bank, load_bank


def _question(text: str) -> QuizQuestion:
    return QuizQuestion(
        question=text, options=["a", "b", "c", "d"], correct_index=0, explanation="x"
    )


def _item(index: int) -> dict:
    return {
        "question": f"Question {index}?",
        "options": ["a", "b", "c", "d"],
        "correct_index": 1,
        "explanation": "x",
    }


def _write_bank_file(directory: Path, items: list[dict]) -> Path:
    path = directory / "quiz_batch_A1.json"
    path.write_text(
        json.dumps(
            {
                "kind": "quiz_batch",
                "level": "A1",
                "language": "ru",
                "generated_at": "2026-10-23T00:00:00+00:00",
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


async def test_generate_writes_valid_file(tmp_path: Path) -> None:
    client = FakeAIClient()
    complete = await generate_bank(
        client,
        language="ru",
        count=5,
        bank_dir=tmp_path,
        levels=("A1",),
        kinds=("quiz_batch",),
    )

    assert complete == 1
    data = json.loads((tmp_path / "quiz_batch_A1.json").read_text(encoding="utf-8"))
    assert data["kind"] == "quiz_batch"
    assert data["level"] == "A1"
    assert data["language"] == "ru"
    assert len(data["items"]) == 5


async def test_generate_covers_all_kinds(tmp_path: Path) -> None:
    client = FakeAIClient()
    complete = await generate_bank(
        client,
        language="ru",
        count=2,
        bank_dir=tmp_path,
        levels=("A1",),
        kinds=("quiz_batch", "scramble", "gap", "race", "truefalse"),
    )

    assert complete == 5
    assert len(list(tmp_path.glob("*.json"))) == 5


async def test_generate_dedupes_and_filters_unsafe(tmp_path: Path) -> None:
    questions = [
        _question("The war question?"),
        _question("Same question?"),
        _question("same   question?"),
        _question("Fresh question?"),
    ]
    client = FakeAIClient(quiz=QuizBatch(questions=questions))
    await generate_bank(
        client,
        language="ru",
        count=2,
        bank_dir=tmp_path,
        levels=("A1",),
        kinds=("quiz_batch",),
    )

    data = json.loads((tmp_path / "quiz_batch_A1.json").read_text(encoding="utf-8"))
    titles = [item["question"] for item in data["items"]]
    assert titles == ["Same question?", "Fresh question?"]


async def test_generate_without_ai_writes_nothing(tmp_path: Path) -> None:
    client = FakeAIClient(error=AIRequestError("down"))
    complete = await generate_bank(
        client,
        language="ru",
        count=2,
        bank_dir=tmp_path,
        levels=("A1",),
        kinds=("quiz_batch",),
    )

    assert complete == 0
    assert list(tmp_path.glob("*.json")) == []


async def test_load_imports_and_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write_bank_file(tmp_path, [_item(1), _item(2), _item(3)])

    first = await load_bank(session_factory, tmp_path)
    second = await load_bank(session_factory, tmp_path)

    assert first == 3
    assert second == 0
    async with session_factory() as session:
        stored = await ContentRepo(session).pick_bank_items("quiz_batch", "A1", "ru", 100)
    assert len(stored) == 3


async def test_load_skips_broken_and_unsafe(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    broken = {
        "question": "Q?",
        "options": ["a", "b", "c"],
        "correct_index": 0,
        "explanation": "x",
    }
    unsafe = _item(9)
    unsafe["question"] = "Who starts a war?"
    _write_bank_file(tmp_path, [_item(1), broken, unsafe])

    added = await load_bank(session_factory, tmp_path)

    assert added == 1
    async with session_factory() as session:
        stored = await ContentRepo(session).pick_bank_items("quiz_batch", "A1", "ru", 100)
    assert len(stored) == 1


async def test_load_ignores_bad_header(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    (tmp_path / "bad.json").write_text(json.dumps({"items": []}), encoding="utf-8")

    assert await load_bank(session_factory, tmp_path) == 0


async def test_load_empty_directory(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    assert await load_bank(session_factory, tmp_path) == 0
