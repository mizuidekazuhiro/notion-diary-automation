from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
NOTION_VERSION = "2022-06-28"


@dataclass(frozen=True)
class ResolvedLocation:
    name: Optional[str]
    source: str
    skip_reason: Optional[str]
    debug_summary: dict[str, Any]


def _safe_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _query_location_log_place(target_date: str, now: datetime) -> tuple[Optional[str], dict[str, Any]]:
    token = os.getenv("NOTION_TOKEN", "").strip()
    location_log_db_id = os.getenv("LOCATION_LOG_DB_ID", "").strip()
    if not token or not location_log_db_id:
        return None, {"enabled": False, "reason": "missing_notion_env"}

    date_property_name = (os.getenv("LOCATION_LOG_TIME_PROP", "").strip() or "Date")
    place_property_name = (os.getenv("LOCATION_LOG_PLACE_PROP", "").strip() or "Place")
    day_start = datetime.fromisoformat(f"{target_date}T00:00:00+09:00")
    day_end = day_start + timedelta(days=1)
    window_end = min(now.astimezone(JST), day_end)
    payload = {
        "filter": {"and": [
            {"property": date_property_name, "date": {"on_or_after": day_start.isoformat()}},
            {"property": date_property_name, "date": {"before": window_end.isoformat()}},
        ]},
        "sorts": [{"property": date_property_name, "direction": "descending"}],
        "page_size": 20,
    }
    try:
        response = requests.post(
            f"https://api.notion.com/v1/databases/{location_log_db_id}/query",
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
            candidate = _rich_text_plain(props.get(place_property_name))
            if candidate:
                return candidate, {
                    "enabled": True,
                    "source": "location_log_db",
                    "candidate_count": len(results),
                    "selected_page_id": page.get("id"),
                    "date_property_name": date_property_name,
                    "place_property_name": place_property_name,
                }
        return None, {
            "enabled": True,
            "reason": "no_place_found",
            "candidate_count": len(results),
            "date_property_name": date_property_name,
            "place_property_name": place_property_name,
        }
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
    target_date = _safe_text(getattr(summary, "target_date", None))
    debug: dict[str, Any] = {}
    if not target_date:
        return ResolvedLocation(
            name=None,
            source="location_log_db",
            skip_reason="missing_target_date",
            debug_summary={"reason": "missing_target_date"},
        )

    location_log_place, location_debug = _query_location_log_place(target_date, now)
    debug["location_log_query"] = location_debug
    if location_log_place:
        return ResolvedLocation(
            name=location_log_place,
            source="location_log_db",
            skip_reason=None,
            debug_summary=debug,
        )
    debug["fallback"] = "location_log_db_only"
    logging.info("weather_location_unresolved debug=%s", debug)
    return ResolvedLocation(
        name=None,
        source="location_log_db",
        skip_reason=location_debug.get("reason", "location_log_db_unavailable"),
        debug_summary=debug,
    )
