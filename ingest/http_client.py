from __future__ import annotations

from typing import Any, Dict, Optional
import time

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

    try:
        resp = _session().post(url, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"post_json failed: url={url}") from e