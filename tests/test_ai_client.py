"""Тесты клиента ИИ: транспорт, статусы, таймаут, пустой ответ."""

import httpx
import pytest

from bot.services.ai_client import AIClient
from bot.services.ai_errors import (
    AIRequestError,
    AIRequestRejected,
    AIResponseError,
    AITimeoutError,
)


def _ok_body(content: str = "hello") -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _make_client(handler, **kwargs) -> AIClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return AIClient(
        "https://ai.test/v1",
        "test-key",
        model="test-model",
        timeout=30.0,
        client=http_client,
        **kwargs,
    )


async def test_send_returns_content_and_sets_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_ok_body("привет"))

    client = _make_client(handler)
    try:
        text = await client._send({"model": "test-model", "messages": []})
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
        await client._send({"model": "test-model", "messages": []})


async def test_server_error_raises_request_error() -> None:
    client = _make_client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(AIRequestError):
        await client._send({"model": "test-model", "messages": []})


async def test_rate_limit_raises_request_error() -> None:
    client = _make_client(lambda request: httpx.Response(429, text="slow down"))
    with pytest.raises(AIRequestError):
        await client._send({"model": "test-model", "messages": []})


async def test_client_error_raises_rejected() -> None:
    client = _make_client(lambda request: httpx.Response(401, text="unauthorized"))
    with pytest.raises(AIRequestRejected):
        await client._send({"model": "test-model", "messages": []})


async def test_empty_content_raises_response_error() -> None:
    body = {"choices": [{"message": {"content": "   "}}]}
    client = _make_client(lambda request: httpx.Response(200, json=body))
    with pytest.raises(AIResponseError):
        await client._send({"model": "test-model", "messages": []})


async def test_non_json_envelope_raises_response_error() -> None:
    client = _make_client(lambda request: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(AIResponseError):
        await client._send({"model": "test-model", "messages": []})
