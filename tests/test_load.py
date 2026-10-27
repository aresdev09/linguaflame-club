"""Нагрузочная эмуляция: 20–50 чатов, лимиты отправки и очередь Sender.

Часы и сон подменяются виртуальными: тест проверяет решения лимитеров
и отсутствие потерянных сообщений, не тратя реальное время на паузы.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from bot.sender import Sender

# Сколько тестовых чатов эмулируем (как в задаче: 20–50).
CHATS = 40

# Настоящий сон: сохраняем до подмены, иначе виртуальный `sleep` зациклится.
_REAL_SLEEP = asyncio.sleep


class _VirtualClock:
    """Виртуальные часы: `monotonic` + мгновенный `sleep`."""

    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += max(seconds, 0.0)
        await _REAL_SLEEP(0)  # отдаём управление другим задачам


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _VirtualClock:
    clock = _VirtualClock()
    monkeypatch.setattr("bot.sender.monotonic", clock.monotonic)
    monkeypatch.setattr("bot.sender.asyncio.sleep", clock.sleep)
    return clock


def _make_sender(**kwargs: object) -> tuple[Sender, AsyncMock]:
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=AsyncMock(message_id=1))
    defaults: dict[str, object] = {
        "per_chat_limit": 20,
        "per_chat_window": 60.0,
        "global_limit": 20,
        "global_window": 1.0,
    }
    defaults.update(kwargs)
    return Sender(bot, **defaults), bot


async def test_many_chats_send_without_losing_messages(clock: _VirtualClock) -> None:
    """40 чатов в пачку: все сообщения доходят, глобальный лимит работает."""
    sender, bot = _make_sender(global_limit=20, global_window=1.0)
    messages_per_chat = 3

    await asyncio.gather(
        *(
            sender.send(-100 - chat, f"hi {chat}")
            for chat in range(CHATS)
            for _ in range(messages_per_chat)
        )
    )

    total = CHATS * messages_per_chat
    assert bot.send_message.await_count == total
    # 120 сообщений по 20 в секунду — минимум 5 секунд виртуального времени.
    assert clock.value >= (total - 20) / 20


async def test_per_chat_limit_is_independent_per_chat(clock: _VirtualClock) -> None:
    """У каждого чата своё окно: переполнение одного не тормозит другой."""
    sender, bot = _make_sender(per_chat_limit=5, per_chat_window=10.0, global_limit=10_000)

    await asyncio.gather(
        *(sender.send(-200, "x") for _ in range(6)),  # ждёт одно окно
        *(sender.send(-201, "y") for _ in range(3)),  # в пределах лимита
    )

    assert bot.send_message.await_count == 9
    assert clock.value == 10.0  # паузу дал только переполненный чат


async def test_burst_to_one_chat_waits_for_window(clock: _VirtualClock) -> None:
    """Серия сверх лимита в один чат ждёт окно, сообщения не теряются."""
    sender, bot = _make_sender(per_chat_limit=5, per_chat_window=10.0, global_limit=10_000)

    await asyncio.gather(*(sender.send(-300, "burst") for _ in range(12)))

    assert bot.send_message.await_count == 12
    # 12 сообщений по 5 в 10 секунд: нужны два полных окна ожидания.
    assert clock.value >= 20.0
