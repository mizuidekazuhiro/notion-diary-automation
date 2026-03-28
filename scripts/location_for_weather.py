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
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    resolution_method: Optional[str] = None


def _safe_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_number(prop: dict[str, Any] | None) -> Optional[float]:
    if not isinstance(prop, dict):
        return None
    ptype = prop.get("type")
    value: Any = None
    if ptype == "number":
        value = prop.get("number")
    elif ptype == "formula":
        formula = prop.get("formula")
        if isinstance(formula, dict) and formula.get("type") == "number":
            value = formula.get("number")
    elif ptype == "rich_text":
        value = _rich_text_plain(prop)
    elif ptype == "title":
        value = _rich_text_plain(prop)
    elif ptype == "select":
        value = _rich_text_plain(prop)
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_valid_coordinate(*, latitude: Optional[float], longitude: Optional[float]) -> bool:
    if latitude is None or longitude is None:
        return False
    if not (-90 <= latitude <= 90):
        return False
    if not (-180 <= longitude <= 180):
        return False
    return True


def _query_location_log_place(target_date: str, now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    token = os.getenv("NOTION_TOKEN", "").strip()
    location_log_db_id = os.getenv("LOCATION_LOG_DB_ID", "").strip()
    date_property_name = (os.getenv("LOCATION_LOG_TIME_PROP", "").strip() or "Time")
    place_property_name = (os.getenv("LOCATION_LOG_PLACE_PROP", "").strip() or "Place")
    if not token or not location_log_db_id:
        return {}, {
            "query_status": "missing_notion_env",
            "notion_token_present": bool(token),
            "location_log_db_id_present": bool(location_log_db_id),
            "effective_time_prop": date_property_name,
            "effective_place_prop": place_property_name,
        }

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
            return {}, {
                "query_status": "notion_error",
                "status_code": response.status_code,
                "notion_token_present": True,
                "location_log_db_id_present": True,
                "effective_time_prop": date_property_name,
                "effective_place_prop": place_property_name,
            }
        data = response.json()
        results = data.get("results", [])
        debug_common = {
            "query_status": "ok",
            "notion_token_present": True,
            "location_log_db_id_present": True,
            "effective_time_prop": date_property_name,
            "effective_place_prop": place_property_name,
            "candidate_count": len(results),
        }
        if not results:
            return {}, {**debug_common, "query_status": "empty_result"}
        for page in results:
            props = page.get("properties", {})
            page_id = page.get("id")
            place_raw = props.get(place_property_name)
            place = _safe_text(_rich_text_plain(place_raw))
            if place:
                return {
                    "name": place,
                    "latitude": None,
                    "longitude": None,
                    "resolution_method": "place_geocoding",
                }, {**debug_common, "selected_page_id": page_id, "selected_label": place, "query_status": "ok"}
        return {}, {
            **debug_common,
            "query_status": "empty_place",
        }
    except Exception as exc:  # noqa: BLE001
        return {}, {
            "query_status": "notion_error",
            "error": str(exc),
            "notion_token_present": bool(token),
            "location_log_db_id_present": bool(location_log_db_id),
            "effective_time_prop": date_property_name,
            "effective_place_prop": place_property_name,
        }


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

    resolved, location_debug = _query_location_log_place(target_date, now)
    debug["location_log_query"] = location_debug
    if resolved.get("name") or (resolved.get("latitude") is not None and resolved.get("longitude") is not None):
        debug["fallback_used"] = "none"
        return ResolvedLocation(
            name=resolved.get("name"),
            source="location_log_db",
            skip_reason=None,
            debug_summary=debug,
            latitude=resolved.get("latitude"),
            longitude=resolved.get("longitude"),
            resolution_method=resolved.get("resolution_method"),
        )
    place = _safe_text(getattr(summary, "place", None))
    if place:
        debug["fallback_used"] = "daily_log_place"
        return ResolvedLocation(name=place, source="daily_log_place", skip_reason=None, debug_summary=debug, resolution_method="place_geocoding")
    location_summary = _safe_text(getattr(summary, "location_summary", None))
    if location_summary:
        debug["fallback_used"] = "daily_log_location_summary"
        return ResolvedLocation(name=location_summary, source="daily_log_location_summary", skip_reason=None, debug_summary=debug, resolution_method="place_geocoding")
    debug["fallback_used"] = "tokyo_default"
    return ResolvedLocation(name="東京都", source="fallback_default_tokyo", skip_reason=None, debug_summary=debug, resolution_method="place_geocoding")
