"""Клиент ИИ: OpenAI-совместимый API (NVIDIA NIM, OpenRouter и другие).

Адрес, ключ и модель берутся только из конфига. Запрос идёт по адресу
`{base_url}/chat/completions`, текст ответа лежит в
`choices[0].message.content` (для JSON-задач — JSON, возможно в обёртке).

Каждый запрос логирует время ответа и расход токенов: точный — из `usage`,
при его отсутствии — грубая оценка по символам. `last_usage` доступен
сервису контента для записи в `ai_usage`.
"""

import json
import logging
import time
from dataclasses import dataclass
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
CHARS_PER_TOKEN = 4  # грубая оценка, если сервер не вернул usage
BOT_TITLE_HEADER = "X-Title"  # необязательный заголовок OpenRouter


@dataclass(frozen=True)
class UsageInfo:
    """Расход токенов одного запроса к ИИ."""

    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        """Сумма токенов — для дневной статистики ai_usage хватает её."""
        return self.prompt_tokens + self.completion_tokens


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
        # Расход токенов последнего запроса; сервис контента пишет его в ai_usage.
        self.last_usage: UsageInfo | None = None

    async def close(self) -> None:
        """Закрывает httpx-клиент, если он создан самим AIClient."""
        if self._owns_client:
            await self._client.aclose()

    async def _send(self, payload: dict[str, Any], *, task: str, prompt: str) -> str:
        """Один HTTP-запрос к ИИ: статусы, разбор конверта, расход токенов."""
        headers = {"Content-Type": "application/json", BOT_TITLE_HEADER: "LinguaFlameClub"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"

        started = time.perf_counter()
        try:
            response = await self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AITimeoutError(f"Таймаут {self.timeout:.0f} с при обращении к ИИ") from exc
        except httpx.TransportError as exc:
            raise AIRequestError(f"ИИ-сервер недоступен: {exc}") from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == RATE_LIMIT_STATUS or response.status_code >= 500:
            raise AIRequestError(
                f"ИИ-сервер вернул {response.status_code}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise AIRequestRejected(
                f"ИИ-сервер отклонил запрос: {response.status_code} {response.text[:200]}"
            )
        data = self._decode(response)
        content = self._extract_content(data)
        usage = self._parse_usage(data, prompt=prompt, content=content)
        self.last_usage = usage
        self._log_usage(task, elapsed_ms, usage)
        return content

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

    @staticmethod
    def _parse_usage(data: dict[str, Any], *, prompt: str, content: str) -> UsageInfo:
        """Точный расход из `usage` или грубая оценка по символам."""
        usage = data.get("usage")
        if (
            isinstance(usage, dict)
            and "prompt_tokens" in usage
            and "completion_tokens" in usage
        ):
            return UsageInfo(
                prompt_tokens=int(usage["prompt_tokens"]),
                completion_tokens=int(usage["completion_tokens"]),
            )
        return UsageInfo(
            prompt_tokens=len(prompt) // CHARS_PER_TOKEN,
            completion_tokens=len(content) // CHARS_PER_TOKEN,
            estimated=True,
        )

    @staticmethod
    def _log_usage(task: str, elapsed_ms: int, usage: UsageInfo) -> None:
        """Пишет в лог время ответа и расход токенов."""
        if usage.estimated:
            logger.info(
                "ИИ %s: %d мс, оценка токенов промпт≈%d ответ≈%d",
                task,
                elapsed_ms,
                usage.prompt_tokens,
                usage.completion_tokens,
            )
            return
        logger.info(
            "ИИ %s: %d мс, токены промпт=%d ответ=%d",
            task,
            elapsed_ms,
            usage.prompt_tokens,
            usage.completion_tokens,
        )
