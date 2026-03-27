from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
NOTION_VERSION = "2022-06-28"


@dataclass(frozen=True)
class ResolvedLocation:
    name: str
    source: str
    debug_summary: dict[str, Any]


def _safe_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _extract_place_from_location_summary(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    patterns = [
        r"([\w\u3040-\u30ff\u3400-\u9fff\-\s]{2,30})(?:に滞在|で過ご|にいた|に立ち寄)",
        r"(?:^|\n)([\w\u3040-\u30ff\u3400-\u9fff\-\s]{2,30})(?:\s*[:：])",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = (match.group(1) or "").strip(" 　。、")
            if candidate:
                return candidate
    return None


def _query_latest_stay_place(now: datetime) -> tuple[Optional[str], dict[str, Any]]:
    token = os.getenv("NOTION_TOKEN", "").strip()
    stay_sessions_db_id = os.getenv("STAY_SESSIONS_DB_ID", "").strip()
    if not token or not stay_sessions_db_id:
        return None, {"enabled": False, "reason": "missing_notion_env"}

    window_start = (now - timedelta(hours=36)).astimezone(JST)
    window_end = now.astimezone(JST)
    payload = {
        "filter": {
            "and": [
                {"property": "SessionStart", "date": {"on_or_before": window_end.isoformat()}},
                {"property": "SessionEnd", "date": {"on_or_after": window_start.isoformat()}},
            ]
        },
        "sorts": [{"property": "SessionEnd", "direction": "descending"}],
        "page_size": 20,
    }
    try:
        response = requests.post(
            f"https://api.notion.com/v1/databases/{stay_sessions_db_id}/query",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            return None, {"enabled": True, "reason": f"notion_error_{response.status_code}"}
        data = response.json()
        results = data.get("results", [])
        for page in results:
            props = page.get("properties", {})
            place_label = _rich_text_plain(props.get("PlaceLabel"))
            name = _rich_text_plain(props.get("Name"))
            candidate = place_label or name
            if candidate:
                return candidate, {"enabled": True, "candidate_count": len(results), "source": "stay_sessions"}
        return None, {"enabled": True, "reason": "no_place_found", "candidate_count": len(results)}
    except Exception as exc:  # noqa: BLE001
        return None, {"enabled": True, "reason": "exception", "error": str(exc)}


def _rich_text_plain(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "title":
        return "".join(x.get("plain_text", "") for x in prop.get("title", []))
    if ptype == "rich_text":
        return "".join(x.get("plain_text", "") for x in prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    return ""


def resolve_location_for_weather(*, summary: Any, now: Optional[datetime] = None) -> ResolvedLocation:
    now = now or datetime.now(JST)
    debug: dict[str, Any] = {}

    latest_stay_place, stay_debug = _query_latest_stay_place(now)
    debug["stay_query"] = stay_debug
    if latest_stay_place:
        return ResolvedLocation(name=latest_stay_place, source="latest_stay_session", debug_summary=debug)

    place = _safe_text(getattr(summary, "place", None))
    if place:
        debug["fallback"] = "daily_log_place"
        return ResolvedLocation(name=place, source="daily_log_place", debug_summary=debug)

    location_summary = _safe_text(getattr(summary, "location_summary", None))
    if location_summary:
        extracted = _extract_place_from_location_summary(location_summary)
        if extracted:
            debug["fallback"] = "location_summary_extracted"
            return ResolvedLocation(name=extracted, source="location_summary_extracted", debug_summary=debug)

    debug["fallback"] = "tokyo_default"
    logging.info("weather_location_fallback debug=%s", debug)
    return ResolvedLocation(name="東京都", source="fallback_tokyo", debug_summary=debug)
