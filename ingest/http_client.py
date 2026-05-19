from __future__ import annotations

import datetime as dt
import email.utils
import json
import logging
import os
import random
import time
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# connect は短め、read は長め（Workers/Notion遅延対策）
DEFAULT_TIMEOUT = (5, 60)  # (connect, read)

# リトライ設定（瞬間的な遅延/429/5xx を吸収）
_RETRY = Retry(
    total=3,
    connect=3,
    read=3,
    status=3,
    backoff_factor=1.0,  # 1s, 2s, 4s...
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "POST"]),
    raise_on_status=False,
)

_SESSION: Optional[requests.Session] = None

FETCH_RETRY_MAX_RETRIES = max(0, int(os.getenv("HTTP_FETCH_MAX_RETRIES", "5") or "5"))
FETCH_RETRY_BACKOFF_BASE_SECONDS = max(
    0.0, float(os.getenv("HTTP_FETCH_BACKOFF_BASE_SECONDS", "2") or "2")
)
FETCH_RETRY_BACKOFF_MAX_SECONDS = max(
    FETCH_RETRY_BACKOFF_BASE_SECONDS,
    float(os.getenv("HTTP_FETCH_BACKOFF_MAX_SECONDS", "60") or "60"),
)
FETCH_RETRY_JITTER_MAX_SECONDS = max(
    0.0, float(os.getenv("HTTP_FETCH_JITTER_MAX_SECONDS", "1") or "1")
)


def _mask_headers(headers: Dict[str, str]) -> Dict[str, str]:
    masked: Dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() == "authorization":
            if value.lower().startswith("bearer "):
                masked[key] = "Bearer ***"
            else:
                masked[key] = "***"
        else:
            masked[key] = value
    return masked


def _format_timeout(timeout: Any) -> str:
    if isinstance(timeout, tuple):
        return f"connect={timeout[0]}s, read={timeout[1]}s"
    return f"{timeout}s"


def _extract_response(response: Optional[requests.Response]) -> tuple[Optional[int], Dict[str, str], str]:
    if response is None:
        return None, {}, ""
    response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in ("content-type", "cf-ray", "server", "x-request-id")
    }
    response_text = response.text[:1000] if response.text else ""
    return response.status_code, response_headers, response_text


def _build_http_error_message(
    *,
    method: str,
    url: str,
    headers: Dict[str, str],
    body_bytes: bytes,
    timeout: Any,
    exc: requests.exceptions.RequestException,
) -> str:
    status_code, response_headers, response_text = _extract_response(
        getattr(exc, "response", None)
    )
    body_size = len(body_bytes)
    exc_type = type(exc).__name__
    return (
        f"HTTP request failed: method={method} url={url} "
        f"headers={json.dumps(_mask_headers(headers), ensure_ascii=False)} "
        f"body_size={body_size} timeout={_format_timeout(timeout)} "
        f"exception_type={exc_type} status_code={status_code} "
        f"response_headers={json.dumps(response_headers, ensure_ascii=False)} "
        f"response_text={json.dumps(response_text, ensure_ascii=False)}"
    )


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        adapter = HTTPAdapter(max_retries=_RETRY, pool_connections=10, pool_maxsize=10)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _SESSION = s
    return _SESSION


def _parse_retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return max(0.0, float(stripped))
    try:
        retry_at = email.utils.parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=dt.timezone.utc)
    delta = (retry_at - dt.datetime.now(dt.timezone.utc)).total_seconds()
    return max(0.0, delta)


def _compute_retry_wait_seconds(response: requests.Response, retry_count: int) -> float:
    retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After"))
    if retry_after is not None:
        return retry_after
    exp_backoff = FETCH_RETRY_BACKOFF_BASE_SECONDS * (2 ** max(0, retry_count - 1))
    jitter = random.uniform(0.0, FETCH_RETRY_JITTER_MAX_SECONDS)
    return min(FETCH_RETRY_BACKOFF_MAX_SECONDS, exp_backoff + jitter)


def fetch_json(url: str, bearer_token: Optional[str]) -> Dict[str, Any]:
    headers: Dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    for retry_count in range(FETCH_RETRY_MAX_RETRIES + 1):
        try:
            resp = _session().get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        except (
            requests.exceptions.MissingSchema,
            requests.exceptions.InvalidURL,
            requests.exceptions.InvalidSchema,
        ) as e:
            logging.error(
                "fetch_json non_retryable_request_exception url=%s error=%s",
                url,
                type(e).__name__,
            )
            raise RuntimeError(f"fetch_json failed: url={url}") from e
        except requests.exceptions.RequestException as e:
            if retry_count < FETCH_RETRY_MAX_RETRIES:
                wait_seconds = min(
                    FETCH_RETRY_BACKOFF_MAX_SECONDS,
                    FETCH_RETRY_BACKOFF_BASE_SECONDS * (2 ** retry_count)
                    + random.uniform(0.0, FETCH_RETRY_JITTER_MAX_SECONDS),
                )
                logging.warning(
                    "fetch_json retrying request_exception retry=%s/%s wait_seconds=%.3f url=%s error=%s",
                    retry_count + 1,
                    FETCH_RETRY_MAX_RETRIES,
                    wait_seconds,
                    url,
                    type(e).__name__,
                )
                time.sleep(wait_seconds)
                continue
            raise RuntimeError(f"fetch_json failed: url={url}") from e

        status = resp.status_code
        if status < 400:
            return resp.json()

        retriable_status = status == 429 or 500 <= status <= 599
        if retriable_status and retry_count < FETCH_RETRY_MAX_RETRIES:
            wait_seconds = _compute_retry_wait_seconds(resp, retry_count + 1)
            logging.warning(
                "fetch_json retrying status_code=%s retry=%s/%s wait_seconds=%.3f url=%s",
                status,
                retry_count + 1,
                FETCH_RETRY_MAX_RETRIES,
                wait_seconds,
                url,
            )
            time.sleep(wait_seconds)
            continue

        response_preview = (resp.text or "")[:300]
        retry_after = resp.headers.get("Retry-After")
        logging.error(
            "fetch_json final_failure=true url=%s status_code=%s retry_after=%s retry_count=%s response_preview=%s",
            url,
            status,
            retry_after,
            retry_count,
            json.dumps(response_preview, ensure_ascii=False),
        )
        try:
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"fetch_json failed: url={url}") from e

    raise RuntimeError(f"fetch_json failed: url={url}")


def post_json(url: str, payload: Dict[str, Any], bearer_token: Optional[str]) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for retry_count in range(FETCH_RETRY_MAX_RETRIES + 1):
        try:
            resp = _session().post(url, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
        except requests.exceptions.RequestException as e:
            if retry_count < FETCH_RETRY_MAX_RETRIES:
                wait_seconds = min(
                    FETCH_RETRY_BACKOFF_MAX_SECONDS,
                    FETCH_RETRY_BACKOFF_BASE_SECONDS * (2 ** retry_count)
                    + random.uniform(0.0, FETCH_RETRY_JITTER_MAX_SECONDS),
                )
                logging.warning(
                    "post_json retrying request_exception retry=%s/%s wait_seconds=%.3f url=%s error=%s",
                    retry_count + 1,
                    FETCH_RETRY_MAX_RETRIES,
                    wait_seconds,
                    url,
                    type(e).__name__,
                )
                time.sleep(wait_seconds)
                continue
            raise RuntimeError(
                _build_http_error_message(
                    method="POST",
                    url=url,
                    headers=headers,
                    body_bytes=body_bytes,
                    timeout=DEFAULT_TIMEOUT,
                    exc=e,
                )
            ) from e

        status = resp.status_code
        if status < 400:
            if not resp.content:
                return {}
            return resp.json()

        retriable_status = status == 429 or 500 <= status <= 599
        if retriable_status and retry_count < FETCH_RETRY_MAX_RETRIES:
            wait_seconds = _compute_retry_wait_seconds(resp, retry_count + 1)
            logging.warning(
                "post_json retrying status_code=%s retry=%s/%s wait_seconds=%.3f url=%s",
                status,
                retry_count + 1,
                FETCH_RETRY_MAX_RETRIES,
                wait_seconds,
                url,
            )
            time.sleep(wait_seconds)
            continue

        try:
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(
                _build_http_error_message(
                    method="POST",
                    url=url,
                    headers=headers,
                    body_bytes=body_bytes,
                    timeout=DEFAULT_TIMEOUT,
                    exc=e,
                )
            ) from e

    raise RuntimeError(f"post_json failed: url={url}")
