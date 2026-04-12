from __future__ import annotations

import logging
import os
import random
import time
from typing import Any

import requests

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.8
DEFAULT_BACKOFF_MAX = 8.0
DEFAULT_API_BASE_URL = "https://api.openai.com/v1"

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid float env %s=%s. Fallback default=%s", name, raw, default)
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid int env %s=%s. Fallback default=%s", name, raw, default)
        return default
    return value if value >= 0 else default


def _estimate_input_size(system_prompt: str, user_prompt: str) -> int:
    return len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8"))


def _retry_sleep_seconds(attempt: int, *, base: float, max_backoff: float) -> float:
    backoff = min(max_backoff, base * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0.0, min(0.5, backoff * 0.25))
    return backoff + jitter


def _extract_message_content(data: dict[str, Any]) -> str:
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_chunks = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        return "".join(text_chunks).strip()
    return ""


def _build_chat_messages(*, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def chat_completion(*, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    connect_timeout = _env_float("OPENAI_CONNECT_TIMEOUT_SEC", DEFAULT_CONNECT_TIMEOUT)
    read_timeout = _env_float("OPENAI_READ_TIMEOUT_SEC", DEFAULT_READ_TIMEOUT)
    max_retries = _env_int("OPENAI_MAX_RETRIES", DEFAULT_MAX_RETRIES)
    backoff_base = _env_float("OPENAI_RETRY_BACKOFF_BASE_SEC", DEFAULT_BACKOFF_BASE)
    backoff_max = _env_float("OPENAI_RETRY_BACKOFF_MAX_SEC", DEFAULT_BACKOFF_MAX)
    base_url = os.getenv("OPENAI_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")
    input_size = _estimate_input_size(system_prompt, user_prompt)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 2):
        started = time.perf_counter()
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "model": model,
                    "temperature": temperature,
                    "messages": _build_chat_messages(system_prompt=system_prompt, user_prompt=user_prompt),
                },
                timeout=(connect_timeout, read_timeout),
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            retryable_status = response.status_code == 429 or 500 <= response.status_code <= 599
            logger.info(
                "[OpenAI] model=%s attempt=%s latency_ms=%s input_bytes=%s status=%s",
                model,
                attempt,
                latency_ms,
                input_size,
                response.status_code,
            )
            if retryable_status:
                response.raise_for_status()
            response.raise_for_status()
            data = response.json()
            content = _extract_message_content(data)
            if not content:
                raise RuntimeError("OpenAI response did not include content")
            return content
        except requests.HTTPError as exc:
            last_error = exc
            status_code = exc.response.status_code if exc.response is not None else None
            retryable = status_code == 429 or (isinstance(status_code, int) and 500 <= status_code <= 599)
        except (requests.ReadTimeout, requests.ConnectionError) as exc:
            last_error = exc
            retryable = True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            retryable = False

        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "[OpenAI] model=%s attempt=%s latency_ms=%s input_bytes=%s retryable=%s error=%s",
            model,
            attempt,
            latency_ms,
            input_size,
            retryable,
            type(last_error).__name__ if last_error else "unknown",
        )
        if (not retryable) or attempt > max_retries:
            break
        time.sleep(_retry_sleep_seconds(attempt, base=backoff_base, max_backoff=backoff_max))

    raise RuntimeError(
        f"OpenAI chat completion failed after retries: {type(last_error).__name__ if last_error else 'unknown_error'}"
    )
