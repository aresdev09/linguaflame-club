"""Тесты клиента ИИ: транспорт, статусы, таймаут, JSON, повторы, токены."""

import json
import logging

import httpx
import pytest

from bot.services.ai_client import (
    AIClient,
    UsageInfo,
    parse_json_payload,
    strip_markdown_fences,
)
from bot.services.ai_errors import (
    AIRequestError,
    AIRequestRejected,
    AIResponseError,
    AITimeoutError,
)


async def _no_sleep(seconds: float) -> None:
    return None


def _payload() -> dict:
    return {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}


def _ok_body(content: str = "hello") -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _quiz_json() -> str:
    return json.dumps(
        {
            "questions": [
                {
                    "question": "She ___ to school.",
                    "options": ["go", "goes", "going", "gone"],
                    "correct_index": 1,
                    "explanation": "С he используется goes.",
                }
            ]
        },
        ensure_ascii=False,
    )


def _make_client(handler, **kwargs) -> AIClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    defaults: dict = {
        "timeout": 30.0,
        "max_retries": 2,
        "retry_delay": 0.0,
        "sleep": _no_sleep,
    }
    defaults.update(kwargs)
    return AIClient(
        "https://ai.test/v1",
        "test-key",
        model="test-model",
        client=http_client,
        **defaults,
    )


# --- транспорт ---


async def test_send_returns_content_and_sets_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_ok_body("привет"))

    client = _make_client(handler)
    try:
        text = await client._send(_payload(), task="test", prompt="hello")
    finally:
        await client.close()

    assert text == "привет"
    assert seen["url"] == "https://ai.test/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"


async def test_timeout_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = _make_client(handler)
    with pytest.raises(AITimeoutError):
        await client._send(_payload(), task="test", prompt="hello")


async def test_server_error_raises_request_error() -> None:
    client = _make_client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(AIRequestError):
        await client._send(_payload(), task="test", prompt="hello")


async def test_rate_limit_raises_request_error() -> None:
    client = _make_client(lambda request: httpx.Response(429, text="slow down"))
    with pytest.raises(AIRequestError):
        await client._send(_payload(), task="test", prompt="hello")


async def test_client_error_raises_rejected() -> None:
    client = _make_client(lambda request: httpx.Response(401, text="unauthorized"))
    with pytest.raises(AIRequestRejected):
        await client._send(_payload(), task="test", prompt="hello")


async def test_empty_content_raises_response_error() -> None:
    body = {"choices": [{"message": {"content": "   "}}]}
    client = _make_client(lambda request: httpx.Response(200, json=body))
    with pytest.raises(AIResponseError):
        await client._send(_payload(), task="test", prompt="hello")


async def test_non_json_envelope_raises_response_error() -> None:
    client = _make_client(lambda request: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(AIResponseError):
        await client._send(_payload(), task="test", prompt="hello")


# --- токены и время ---


async def test_usage_is_logged_with_exact_tokens(caplog: pytest.LogCaptureFixture) -> None:
    body = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    client = _make_client(lambda request: httpx.Response(200, json=body))

    with caplog.at_level(logging.INFO, logger="bot.services.ai_client"):
        await client._send(_payload(), task="quiz", prompt="hello")

    assert "токены промпт=10 ответ=20" in caplog.text
    assert client.last_usage == UsageInfo(prompt_tokens=10, completion_tokens=20)
    assert client.last_usage is not None
    assert client.last_usage.total_tokens == 30


async def test_usage_is_estimated_when_missing(caplog: pytest.LogCaptureFixture) -> None:
    client = _make_client(lambda request: httpx.Response(200, json=_ok_body("answer")))

    with caplog.at_level(logging.INFO, logger="bot.services.ai_client"):
        await client._send(_payload(), task="quiz", prompt="x" * 40)

    assert "оценка токенов" in caplog.text
    assert client.last_usage is not None
    assert client.last_usage.estimated is True


# --- JSON, повторы, резервная модель ---


async def test_generate_quiz_batch_parses_valid_json() -> None:
    client = _make_client(lambda request: httpx.Response(200, json=_ok_body(_quiz_json())))
    batch = await client.generate_quiz_batch("B1", "ru")
    assert len(batch.questions) == 1
    assert batch.questions[0].correct_index == 1


async def test_generate_parses_markdown_wrapped_json() -> None:
    content = f"```json\n{_quiz_json()}\n```"
    client = _make_client(lambda request: httpx.Response(200, json=_ok_body(content)))
    batch = await client.generate_quiz_batch("B1", "ru")
    assert batch.questions


async def test_broken_json_is_retried_then_raises() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, json=_ok_body("sorry, I cannot do that"))

    client = _make_client(handler)
    with pytest.raises(AIResponseError):
        await client.generate_quiz_batch("B1", "ru")
    assert len(attempts) == 3  # первая попытка и два повтора


async def test_broken_json_recovers_on_retry() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(200, json=_ok_body("not a json"))
        return httpx.Response(200, json=_ok_body(_quiz_json()))

    client = _make_client(handler)
    batch = await client.generate_quiz_batch("B1", "ru")
    assert batch.questions
    assert len(attempts) == 2


async def test_empty_answer_is_retried_and_fails() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": " "}}]})

    client = _make_client(handler)
    with pytest.raises(AIResponseError):
        await client.generate_quiz_batch("B1", "ru")
    assert len(attempts) == 3


async def test_timeout_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("timed out", request=request)

    client = _make_client(handler)
    with pytest.raises(AITimeoutError):
        await client.generate_quiz_batch("B1", "ru")
    assert len(calls) == 1


async def test_fallback_model_used_after_primary_failure() -> None:
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        models.append(body["model"])
        if body["model"] == "test-model":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=_ok_body(_quiz_json()))

    client = _make_client(handler, fallback_model="backup-model")
    batch = await client.generate_quiz_batch("B1", "ru")
    assert batch.questions
    assert models == ["test-model", "test-model", "test-model", "backup-model"]


def test_parse_json_payload_handles_fences_and_extra_text() -> None:
    assert parse_json_payload('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_payload('Here you go: {"a": 2} thanks') == {"a": 2}
    with pytest.raises(json.JSONDecodeError):
        parse_json_payload("no json here")
    assert strip_markdown_fences("```\nvalue\n```") == "value"
