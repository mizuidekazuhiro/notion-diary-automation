from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ingest.http_client import post_json


@dataclass(frozen=True)
class EnsureResult:
    page_id: str


def ensure_daily_log_page(
    *,
    ensure_url: str,
    target_date: str,
    title: str,
    source: str,
    mail_id: str,
    bearer_token: Optional[str],
) -> EnsureResult:
    payload = {
        "target_date": target_date,
        "title": title,
        "source": source,
        "mail_id": mail_id,
    }
    response = post_json(ensure_url, payload, bearer_token)
    page_id = response.get("page_id", "")
    if not page_id:
        raise RuntimeError("ensure_daily_log_page: missing page_id in response")
    return EnsureResult(page_id=page_id)
