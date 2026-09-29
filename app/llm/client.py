import asyncio
import random
import time

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.core.config import settings
from app.core.context import current_job_id, current_request_id
from app.core.db import run_db
from app.core.logging import get_logger
from app.services.cost import calc_cost


class LlmError(RuntimeError):
    pass


def setup_llm() -> None:
    """Имя из каркаса Хекслета. Точка для патча в тестах."""
    return None


class Runner:
    """Заглушка имени из каркаса (openai-agents Runner) для патча в тестах."""

    @staticmethod
    async def run(agent, prompt: str):
        class _Result:
            final_output = ""

        return _Result()


def _agent_retryable(exc: Exception) -> bool:
    """Ретраим только транспортные и серверные ошибки: 429, 5xx, таймаут, сеть."""
    if isinstance(exc, (APIConnectionError, APITimeoutError, TimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


async def run_agent(agent, prompt: str):
    """Запустить агента с ретраями: экспоненциальная задержка + jitter."""
    setup_llm()
    for attempt in range(1, settings.llm_max_retries + 1):
        try:
            result = await asyncio.wait_for(
                Runner.run(agent, prompt),
                timeout=settings.llm_timeout_seconds,
            )
            return getattr(result, "final_output", "") or str(result)
        except Exception as exc:
            if attempt >= settings.llm_max_retries or not _agent_retryable(exc):
                get_logger().error("llm_call_failed", attempt=attempt, error=repr(exc))
                raise
            delay = min(2**attempt * 0.5, 8.0) * (1 + random.random() * 0.25)
            get_logger().warning("llm_call_retry", attempt=attempt, delay=round(delay, 2))
            await asyncio.sleep(delay)
    raise RuntimeError("llm retries exhausted")


class LlmClient:
    """Вызов модели за одной границей: таймаут, ретраи, OpenAI-совместимый API."""

    def __init__(self) -> None:
        self._base_url = settings.llm_base_url.rstrip("/")
        self._timeout = settings.llm_timeout_seconds
        self._retries = settings.llm_max_retries
        self._headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

    def complete(
        self,
        system: str,
        user: str,
        *,
        schema: dict | None = None,
        cheap: bool = False,
    ) -> str:
        model = settings.llm_cheap_model if cheap else settings.llm_model
        payload: dict = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": str(schema.get("title") or "Result"),
                    "schema": schema,
                    "strict": False,
                },
            }
        last_error: Exception | None = None
        attempts = max(1, self._retries)
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                response = httpx.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=self._headers,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                data = response.json()
                duration_ms = int((time.perf_counter() - started) * 1000)
                usage = data.get("usage") or {}
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                cost = calc_cost(model, prompt_tokens, completion_tokens)
                _record_call(
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost=cost,
                    duration_ms=duration_ms,
                )
                get_logger().info(
                    "llm_call",
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost=str(cost),
                    duration_ms=duration_ms,
                    job_id=current_job_id(),
                    request_id=current_request_id(),
                )
                return data["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                last_error = exc
                if attempt >= attempts or not _retryable(exc):
                    break
                delay = min(2**attempt * 0.5, 8.0) * (1 + random.random() * 0.25)
                time.sleep(delay)
        raise LlmError(str(last_error))


def _record_call(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost,
    duration_ms: int,
) -> None:
    from app.repositories import llm_calls

    try:
        run_db(
            llm_calls.insert_call(
                job_id=current_job_id(),
                request_id=current_request_id(),
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                duration_ms=duration_ms,
            )
        )
    except Exception as exc:
        get_logger().warning("llm_call_write_failed", error=str(exc))


def _retryable(exc: Exception) -> bool:
    """Повтор лечит обрыв, таймаут, 429 и 5xx. Ошибка запроса — нет."""
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    if isinstance(exc, (APIConnectionError, APITimeoutError, TimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False
