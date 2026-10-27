"""Клиент ИИ: OpenAI-совместимый API (NVIDIA NIM, OpenRouter и другие).

Адрес, ключ и модель берутся только из конфига. Запрос идёт по адресу
`{base_url}/chat/completions`, текст ответа лежит в
`choices[0].message.content` (для JSON-задач — JSON, возможно в обёртке).

Надёжность: таймаут из конфига, до двух повторов с экспоненциальной паузой
(битый JSON и 5xx/429), резервная модель после отказа основной. Каждый
запрос логирует время ответа и токены; `last_usage` пишет сервис контента.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from bot.config import Settings
from bot.prompts import tasks
from bot.prompts.base import build_system_prompt
from bot.services.ai_errors import (
    AIError,
    AIRequestError,
    AIRequestRejected,
    AIResponseError,
    AITimeoutError,
)
from bot.services.ai_quality import require_word_count
from bot.services.ai_schemas import (
    GapResponse,
    GrammarLesson,
    QuizBatch,
    RaceResponse,
    ScrambleResponse,
    TrueFalseResponse,
    WordsResponse,
)

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0
RATE_LIMIT_STATUS = 429  # у моделей с ограничениями это обычное дело
CHARS_PER_TOKEN = 4  # грубая оценка, если сервер не вернул usage
BOT_TITLE_HEADER = "X-Title"  # необязательный заголовок OpenRouter

SchemaT = TypeVar("SchemaT", bound=BaseModel)


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


def strip_markdown_fences(text: str) -> str:
    """Убирает обёртку ```json ... ``` вокруг ответа модели."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*[ \t]*", "", cleaned)
        cleaned = re.sub(r"[ \t]*```$", "", cleaned)
    return cleaned.strip()


def parse_json_payload(text: str) -> Any:
    """Разбирает JSON: снимает markdown-обёртку, иначе ищет объект в тексте."""
    cleaned = strip_markdown_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


class AIClient:
    """Клиент генерации контента: транспорт, схемы, повторы, резервная модель."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str,
        fallback_model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        retry_delay: float = RETRY_BASE_DELAY,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model or None
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_delay = retry_delay
        self._sleep = sleep
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        # Расход токенов последнего запроса; сервис контента пишет его в ai_usage.
        self.last_usage: UsageInfo | None = None

    async def close(self) -> None:
        """Закрывает httpx-клиент, если он создан самим AIClient."""
        if self._owns_client:
            await self._client.aclose()

    # --- задачи бота ---

    async def generate_quiz_batch(
        self, level: str, language: str, *, count: int = tasks.QUIZ_BATCH_SIZE
    ) -> QuizBatch:
        """Пачка вопросов квиза для уровня и языка группы."""
        return await self._request_json(
            QuizBatch,
            task="quiz_batch",
            prompt=tasks.quiz_batch_prompt(count),
            level=level,
            language=language,
        )

    async def generate_words(
        self,
        level: str,
        language: str,
        *,
        count: int,
        exclude_words: Iterable[str] = (),
    ) -> WordsResponse:
        """Слова дня без недавно использованных слов уровня."""
        return await self._request_json(
            WordsResponse,
            task="words",
            prompt=tasks.words_prompt(count, exclude_words),
            level=level,
            language=language,
            validate=require_word_count(count),
        )

    async def generate_grammar(self, level: str, language: str) -> GrammarLesson:
        """Короткий урок грамматики для уровня."""
        return await self._request_json(
            GrammarLesson,
            task="grammar",
            prompt=tasks.grammar_prompt(),
            level=level,
            language=language,
        )

    async def generate_scramble(
        self, level: str, language: str, *, count: int = tasks.GAME_BATCH_SIZE
    ) -> ScrambleResponse:
        """Слова для игры «Собери слово»."""
        return await self._request_json(
            ScrambleResponse,
            task="scramble",
            prompt=tasks.scramble_prompt(count),
            level=level,
            language=language,
        )

    async def generate_gap(
        self, level: str, language: str, *, count: int = tasks.GAME_BATCH_SIZE
    ) -> GapResponse:
        """Предложения с пропуском для игры «Пропущенное слово»."""
        return await self._request_json(
            GapResponse,
            task="gap",
            prompt=tasks.gap_prompt(count),
            level=level,
            language=language,
        )

    async def generate_race(
        self, level: str, language: str, *, count: int = tasks.GAME_BATCH_SIZE
    ) -> RaceResponse:
        """Слова с вариантами перевода для гонки переводов."""
        return await self._request_json(
            RaceResponse,
            task="race",
            prompt=tasks.race_prompt(count),
            level=level,
            language=language,
        )

    async def generate_truefalse(
        self, level: str, language: str, *, count: int = tasks.GAME_BATCH_SIZE
    ) -> TrueFalseResponse:
        """Утверждения «правда или ложь» для уровня."""
        return await self._request_json(
            TrueFalseResponse,
            task="truefalse",
            prompt=tasks.truefalse_prompt(count),
            level=level,
            language=language,
        )

    # --- транспорт ---

    def _model_candidates(self) -> list[str]:
        """Основная модель и, если задана, резервная — без дублей."""
        candidates = [self.model]
        if self.fallback_model and self.fallback_model not in candidates:
            candidates.append(self.fallback_model)
        return candidates

    def _payload(self, *, level: str, language: str, prompt: str, model: str) -> dict[str, Any]:
        """Тело запроса в формате OpenAI: модель, системный промпт, задача."""
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": build_system_prompt(level, language)},
                {"role": "user", "content": prompt},
            ],
        }

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
            raise AIRequestError(f"ИИ-сервер вернул {response.status_code}: {response.text[:200]}")
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

    async def _before_retry(self, attempt: int, task: str, error: Exception) -> None:
        """Экспоненциальная пауза перед повтором: base, base*2, base*4."""
        delay = self.retry_delay * 2 ** (attempt - 1)
        logger.warning(
            "ИИ %s: %s. Повтор %d из %d через %.1f с",
            task,
            error,
            attempt,
            self.max_retries,
            delay,
        )
        await self._sleep(delay)

    async def _request_json(
        self,
        schema: type[SchemaT],
        *,
        task: str,
        prompt: str,
        level: str,
        language: str,
        validate: Callable[[SchemaT], None] | None = None,
    ) -> SchemaT:
        """Ответ как JSON нужной схемы; повторы и резервная модель при сбоях."""
        last_error: AIError | None = None
        for model_index, current_model in enumerate(self._model_candidates()):
            if model_index:
                logger.warning(
                    "ИИ %s: переключаюсь на резервную модель %s после ошибки: %s",
                    task,
                    current_model,
                    last_error or "неизвестная ошибка",
                )
            payload = self._payload(
                level=level, language=language, prompt=prompt, model=current_model
            )
            for attempt in range(self.max_retries + 1):
                if attempt:
                    await self._before_retry(attempt, task, last_error or AIError("?"))
                try:
                    text = await self._send(payload, task=task, prompt=prompt)
                except AITimeoutError as exc:
                    last_error = exc
                    break  # повтор на той же модели только удвоит ожидание
                except AIRequestRejected as exc:
                    last_error = exc
                    break  # 4xx повторять бессмысленно
                except (AIRequestError, AIResponseError) as exc:
                    last_error = exc
                    continue
                try:
                    result = schema.model_validate(parse_json_payload(text))
                    if validate is not None:
                        validate(result)
                    return result
                except ValueError as exc:  # битый JSON, схема или проверка контента
                    last_error = AIResponseError(
                        f"Ответ ИИ не подходит под схему {schema.__name__}: {exc}"
                    )
                    logger.warning("Ответ ИИ невалиден (%s): %s", schema.__name__, exc)
        raise last_error or AIError("ИИ не ответил")

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
        if isinstance(usage, dict) and "prompt_tokens" in usage and "completion_tokens" in usage:
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


def build_ai_client(settings: Settings) -> AIClient:
    """Клиент из настроек: адрес, ключ и модели только из конфига."""
    return AIClient(
        base_url=settings.ai_server_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        fallback_model=settings.ai_model_fallback or None,
        timeout=settings.ai_timeout_seconds,
    )
