from typing import Any, Dict, Optional

import requests


def post_json(url: str, payload: Dict[str, Any], bearer_token: Optional[str]) -> None:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()


def upsert_daily_log(
    url: str, payload: Dict[str, Any], bearer_token: Optional[str]
) -> None:
    post_json(url, payload, bearer_token)
