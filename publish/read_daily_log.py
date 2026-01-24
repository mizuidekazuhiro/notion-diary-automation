from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from ingest.http_client import fetch_json


@dataclass(frozen=True)
class DailyLogSummary:
    target_date: str
    page_id: str
    title: str
    summary_text: str
    summary_html: str
    mail_id: str
    source: Optional[str]
    diary: Optional[str]
    expenses_total: Optional[float]
    location_summary: Optional[str]
    mood: Optional[str]
    weight: Optional[float]


def read_daily_log(
    *, daily_log_read_url: str, target_date: str, bearer_token: Optional[str]
) -> Optional[DailyLogSummary]:
    url = f"{daily_log_read_url}?{urlencode({'date': target_date})}"
    payload = fetch_json(url, bearer_token)
    if not payload.get("found"):
        return None

    return DailyLogSummary(
        target_date=payload.get("target_date", target_date),
        page_id=payload.get("page_id", ""),
        title=payload.get("title", ""),
        summary_text=payload.get("summary_text", ""),
        summary_html=payload.get("summary_html", ""),
        mail_id=payload.get("mail_id", ""),
        source=payload.get("source"),
        diary=payload.get("diary"),
        expenses_total=payload.get("expenses_total"),
        location_summary=payload.get("location_summary"),
        mood=payload.get("mood"),
        weight=payload.get("weight"),
    )
