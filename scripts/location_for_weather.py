from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
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


def _parse_number(prop: dict[str, Any] | None) -> Optional[float]:
    if not isinstance(prop, dict):
        return None
    if prop.get("type") == "number":
        value = prop.get("number")
    else:
        value = _rich_text_plain(prop)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_valid_coordinate(*, latitude: Optional[float], longitude: Optional[float]) -> bool:
    if latitude is None or longitude is None:
        return False
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def _location_query_debug_base(*, time_prop: str, place_prop: str, token: str, db_id: str) -> dict[str, Any]:
    return {
        "query_status": "unknown",
        "notion_token_present": bool(token),
        "location_log_db_id_present": bool(db_id),
        "effective_time_prop": time_prop,
        "effective_place_prop": place_prop,
        "effective_lat_prop": "Latitude (raw)",
        "effective_lon_prop": "Longitude (raw)",
        # backward compatibility for older debug consumers
        "resolved_lat_prop": "Latitude (raw)",
        "resolved_lon_prop": "Longitude (raw)",
        "latest_selected_page_id": None,
        "latest_selected_time": None,
        "selected_label": None,
        "latlon_available": False,
        "geocode_attempted": False,
        "geocode_query": None,
        "fallback_used": "none",
        "weather_status": "pending",
    }


def _query_location_log_place(now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    del now
    token = os.getenv("NOTION_TOKEN", "").strip()
    location_log_db_id = os.getenv("LOCATION_LOG_DB_ID", "").strip()
    time_prop = os.getenv("LOCATION_LOG_TIME_PROP", "").strip() or "Time"
    place_prop = os.getenv("LOCATION_LOG_PLACE_PROP", "").strip() or "Place"
    debug = _location_query_debug_base(time_prop=time_prop, place_prop=place_prop, token=token, db_id=location_log_db_id)

    if not token or not location_log_db_id:
        debug["query_status"] = "missing_notion_env"
        debug["weather_status"] = "skipped"
        return {}, debug

    payload = {
        "filter": {
            "and": [
                {"property": time_prop, "date": {"is_not_empty": True}},
            ]
        },
        "sorts": [{"property": time_prop, "direction": "descending"}],
        "page_size": 1,
    }
    debug["query_payload"] = payload

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
    except Exception as exc:  # noqa: BLE001
        debug.update(
            {
                "query_status": "notion_error",
                "weather_status": "skipped",
                "query_exception_class": exc.__class__.__name__,
                "query_exception_message": str(exc),
            }
        )
        return {}, debug

    if response.status_code >= 500:
        debug.update({"query_status": "notion_error", "status_code": response.status_code, "weather_status": "skipped"})
        return {}, debug
    if response.status_code >= 400:
        debug.update({"query_status": "query_failed", "status_code": response.status_code, "weather_status": "skipped"})
        return {}, debug

    data = response.json() if hasattr(response, "json") else {}
    results = data.get("results", []) if isinstance(data, dict) else []
    if not results:
        debug.update({"query_status": "no_results", "weather_status": "skipped"})
        return {}, debug

    page = results[0]
    props = page.get("properties", {}) if isinstance(page, dict) else {}
    place = _safe_text(_rich_text_plain(props.get(place_prop)))
    latitude = _parse_number(props.get("Latitude (raw)"))
    longitude = _parse_number(props.get("Longitude (raw)"))
    latest_time = _safe_text(((props.get(time_prop) or {}).get("date") or {}).get("start"))
    debug.update(
        {
            "query_status": "ok",
            "weather_status": "ok",
            "latest_selected_page_id": page.get("id") if isinstance(page, dict) else None,
            "latest_selected_time": latest_time,
            "selected_label": place,
            "latlon_available": _is_valid_coordinate(latitude=latitude, longitude=longitude),
        }
    )

    return {
        "name": place,
        "selected_place": place,
        "latitude": latitude,
        "longitude": longitude,
        "resolution_method": "location_log_latest_latlon"
        if _is_valid_coordinate(latitude=latitude, longitude=longitude)
        else "location_log_latest_place",
    }, debug






def _normalize_geocode_query(place: str) -> str:
    text = (place or "").strip()
    import re

    text = re.sub(r"〒\s*\d{3}-?\d{4}", "", text)
    text = text.replace("、", " ").replace(",", " ")
    text = re.sub(r"\b(Japan|日本国|日本)\b\s*$", "", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text.strip()

def _load_geocode_cache() -> dict[str, dict[str, float]]:
    return {}


def _save_geocode_cache(_cache: dict[str, dict[str, float]]) -> None:
    return None


def _geocode_place(place: str) -> tuple[Optional[float], Optional[float], dict[str, Any]]:
    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": place, "count": 1, "language": "ja", "format": "json"},
            timeout=20,
        )
        if response.status_code >= 400:
            return None, None, {"status": "failed", "reason": f"http_{response.status_code}"}
        data = response.json() if hasattr(response, "json") else {}
        results = (data.get("results") if isinstance(data, dict) else None) or []
        if not results:
            return None, None, {"status": "failed", "reason": "geocoding_no_results"}
        first = results[0]
        lat = float(first.get("latitude"))
        lon = float(first.get("longitude"))
        if not _is_valid_coordinate(latitude=lat, longitude=lon):
            return None, None, {"status": "failed", "reason": "invalid_geocode_coordinates"}
        return lat, lon, {"status": "ok", "resolved_name": first.get("name")}
    except Exception as exc:  # noqa: BLE001
        return None, None, {"status": "failed", "reason": f"geocoding_exception:{exc.__class__.__name__}"}

def _build_debug(location_log_query: dict[str, Any]) -> dict[str, Any]:
    debug = {"location_log_query": location_log_query}
    # backward compatibility: keep flattened keys too
    debug.update(location_log_query)
    return debug


def resolve_location_for_weather(*, summary: Any, now: Optional[datetime] = None) -> ResolvedLocation:
    now = now or datetime.now(JST)
    resolved, location_log_query = _query_location_log_place(now)

    if _is_valid_coordinate(latitude=resolved.get("latitude"), longitude=resolved.get("longitude")):
        return ResolvedLocation(
            name=resolved.get("name") or resolved.get("selected_place"),
            source="location_log_db_latest",
            skip_reason=None,
            debug_summary=_build_debug(location_log_query),
            latitude=resolved.get("latitude"),
            longitude=resolved.get("longitude"),
            resolution_method="location_log_latest_latlon",
        )

    latest_place = _safe_text(resolved.get("selected_place") or resolved.get("name"))
    if latest_place:
        location_log_query["geocode_attempted"] = True
        location_log_query["geocode_query"] = latest_place
        cache = _load_geocode_cache()
        normalized_place = _normalize_geocode_query(latest_place)
        location_log_query["geocode_query"] = normalized_place
        cache_item = cache.get(normalized_place)
        if cache_item and _is_valid_coordinate(latitude=cache_item.get("lat"), longitude=cache_item.get("lon")):
            location_log_query["fallback_used"] = "location_log_latest_place_geocode_cache"
            return ResolvedLocation(
                name=latest_place,
                source="location_log_db_latest",
                skip_reason=None,
                debug_summary=_build_debug(location_log_query),
                latitude=cache_item.get("lat"),
                longitude=cache_item.get("lon"),
                resolution_method="place_geocoding",
            )

        latitude, longitude, geocode_debug = _geocode_place(normalized_place)
        location_log_query["geocode_debug"] = geocode_debug
        resolution_method = "place_geocoding"
        if _is_valid_coordinate(latitude=latitude, longitude=longitude):
            cache[normalized_place] = {"lat": float(latitude), "lon": float(longitude)}
            _save_geocode_cache(cache)
            resolution_method = "location_log_latest_place_geocode"
        location_log_query["fallback_used"] = "location_log_latest_place_geocode"
        return ResolvedLocation(
            name=latest_place,
            source="location_log_db_latest",
            skip_reason=None,
            debug_summary=_build_debug(location_log_query),
            latitude=latitude,
            longitude=longitude,
            resolution_method=resolution_method,
        )

    place = _safe_text(getattr(summary, "place", None))
    if place:
        location_log_query["fallback_used"] = "daily_log_place"
        return ResolvedLocation(
            name=place,
            source="daily_log_place",
            skip_reason=None,
            debug_summary=_build_debug(location_log_query),
            resolution_method="daily_log_place",
        )

    location_summary = _safe_text(getattr(summary, "location_summary", None))
    if location_summary:
        location_log_query["fallback_used"] = "daily_log_location_summary"
        return ResolvedLocation(
            name=location_summary,
            source="daily_log_location_summary",
            skip_reason=None,
            debug_summary=_build_debug(location_log_query),
            resolution_method="daily_log_location_summary",
        )

    location_log_query["fallback_used"] = "tokyo_default"
    return ResolvedLocation(
        name="東京都",
        source="fallback_default_tokyo",
        skip_reason=None,
        debug_summary=_build_debug(location_log_query),
        resolution_method="tokyo_default",
    )
