"""Клиент ИИ: OpenAI-совместимый API (NVIDIA NIM, OpenRouter и другие).

Адрес, ключ и модель берутся только из конфига. Запрос идёт по адресу
`{base_url}/chat/completions`, текст ответа лежит в
`choices[0].message.content` (для JSON-задач — JSON, возможно в обёртке).
"""

import json
import logging
from typing import Any

import httpx

from bot.services.ai_errors import (
    AIRequestError,
    AIRequestRejected,
    AIResponseError,
    AITimeoutError,
)

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_TIMEOUT = 30.0
RATE_LIMIT_STATUS = 429  # у моделей с ограничениями это обычное дело
BOT_TITLE_HEADER = "X-Title"  # необязательный заголовок OpenRouter


class AIClient:
    """Транспорт до ИИ: таймаут, разбор конверта OpenAI, понятные ошибки."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str,
        fallback_model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model or None
        self.timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def close(self) -> None:
        """Закрывает httpx-клиент, если он создан самим AIClient."""
        if self._owns_client:
            await self._client.aclose()

    async def _send(self, payload: dict[str, Any]) -> str:
        """Один HTTP-запрос к ИИ: статусы и разбор конверта."""
        headers = {"Content-Type": "application/json", BOT_TITLE_HEADER: "LinguaFlameClub"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"

        try:
            response = await self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AITimeoutError(f"Таймаут {self.timeout:.0f} с при обращении к ИИ") from exc
        except httpx.TransportError as exc:
            raise AIRequestError(f"ИИ-сервер недоступен: {exc}") from exc

        if response.status_code == RATE_LIMIT_STATUS or response.status_code >= 500:
            raise AIRequestError(
                f"ИИ-сервер вернул {response.status_code}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise AIRequestRejected(
                f"ИИ-сервер отклонил запрос: {response.status_code} {response.text[:200]}"
            )
        return self._extract_content(self._decode(response))

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        """Разбирает конверт OpenAI; не-JSON тело — ошибка ответа."""
        try:
            data = response.json()
        except ValueError as exc:
            raise AIResponseError("ИИ вернул не JSON") from exc
        if not isinstance(data, dict):
            raise AIResponseError("ИИ вернул не объект")
        return data

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        """Достаёт текст ответа из choices[0].message.content."""
        choices = data.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            reason = data.get("error") or data
            raise AIResponseError(
                f"В ответе ИИ нет текста: {json.dumps(reason, ensure_ascii=False)[:200]}"
            )
        return content
