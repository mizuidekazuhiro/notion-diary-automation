from __future__ import annotations

import json
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


def fetch_json(url: str, bearer_token: Optional[str]) -> Dict[str, Any]:
    headers: Dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    try:
        resp = _session().get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        # どのURLで落ちたかを必ず残す（Actionsログで原因特定しやすい）
        raise RuntimeError(f"fetch_json failed: url={url}") from e


def post_json(url: str, payload: Dict[str, Any], bearer_token: Optional[str]) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    try:
        resp = _session().post(url, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()
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
