from __future__ import annotations

from typing import Any, Dict, Optional

import requests


def fetch_json(url: str, bearer_token: Optional[str]) -> Dict[str, Any]:
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def post_json(
    url: str, payload: Dict[str, Any], bearer_token: Optional[str]
) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()
