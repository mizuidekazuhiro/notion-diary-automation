from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
NOTION_VERSION = "2022-06-28"
GEOCODE_CACHE_PATH = Path(os.getenv("WEATHER_GEOCODE_CACHE_PATH", ".runtime/weather_geocode_cache.json"))


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
    date_property_name_env = os.getenv("LOCATION_LOG_TIME_PROP", "").strip()
    place_property_name_env = os.getenv("LOCATION_LOG_PLACE_PROP", "").strip()
    lat_property_name = os.getenv("LOCATION_LOG_LAT_PROP", "").strip()
    lon_property_name = os.getenv("LOCATION_LOG_LON_PROP", "").strip()
    if not token or not location_log_db_id:
        return {}, {
            "query_status": "missing_notion_env",
            "notion_token_present": bool(token),
            "location_log_db_id_present": bool(location_log_db_id),
            "effective_time_prop": date_property_name_env or "Time",
            "effective_place_prop": place_property_name_env or "Place",
        }

    schema, schema_debug = _fetch_schema(token=token, db_id=location_log_db_id)
    resolved_time_name, resolved_time_debug = _resolve_prop_name(
        env_value=(date_property_name_env or None),
        aliases=["Time", "time", "日時", "Date", "date"],
        schema=schema,
    )
    resolved_place_name, resolved_place_debug = _resolve_prop_name(
        env_value=(place_property_name_env or None),
        aliases=["Place", "place", "場所", "Location", "location"],
        schema=schema,
    )
    resolved_lat_name, resolved_lat_debug = _resolve_prop_name(
        env_value=lat_property_name or None,
        aliases=["Latitude", "Lat", "latitude", "lat", "緯度"],
        schema=schema,
    )
    resolved_lon_name, resolved_lon_debug = _resolve_prop_name(
        env_value=lon_property_name or None,
        aliases=["Longitude", "Lon", "Lng", "longitude", "lon", "lng", "経度"],
        schema=schema,
    )

    if not resolved_time_name or not resolved_place_name:
        return {}, {
            "query_status": "schema_unresolved",
            "schema_fetch": schema_debug,
            "resolved_props": {
                "time": {"resolved_name": resolved_time_name, **resolved_time_debug},
                "place": {"resolved_name": resolved_place_name, **resolved_place_debug},
                "latitude": {"resolved_name": resolved_lat_name, **resolved_lat_debug},
                "longitude": {"resolved_name": resolved_lon_name, **resolved_lon_debug},
            },
            "reason": "required_props_unresolved",
        }

    day_start = datetime.fromisoformat(f"{target_date}T00:00:00+09:00")
    day_end = day_start + timedelta(days=1)
    window_end = min(now.astimezone(JST), day_end)
    payload = {
        "filter": {"and": [
            {"property": resolved_time_name, "date": {"on_or_after": day_start.isoformat()}},
            {"property": resolved_time_name, "date": {"before": window_end.isoformat()}},
        ]},
        "sorts": [{"property": resolved_time_name, "direction": "descending"}],
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
                "effective_time_prop": resolved_time_name,
                "effective_place_prop": resolved_place_name,
            }
        data = response.json()
        results = data.get("results", [])
        debug_common = {
            "query_status": "ok",
            "notion_token_present": True,
            "location_log_db_id_present": True,
            "effective_time_prop": resolved_time_name,
            "effective_place_prop": resolved_place_name,
            "effective_lat_prop": resolved_lat_name,
            "effective_lon_prop": resolved_lon_name,
            "candidate_count": len(results),
            "resolved_props": {
                "time": {"resolved_name": resolved_time_name, **resolved_time_debug},
                "place": {"resolved_name": resolved_place_name, **resolved_place_debug},
                "latitude": {"resolved_name": resolved_lat_name, **resolved_lat_debug},
                "longitude": {"resolved_name": resolved_lon_name, **resolved_lon_debug},
            },
        }
        if not results:
            return {}, {**debug_common, "query_status": "empty_result"}
        for page in results:
            props = page.get("properties", {})
            page_id = page.get("id")
            place_raw = props.get(resolved_place_name)
            place = _safe_text(_rich_text_plain(place_raw))
            latitude = _parse_number(props.get(resolved_lat_name) if resolved_lat_name else None)
            longitude = _parse_number(props.get(resolved_lon_name) if resolved_lon_name else None)
            if _is_valid_coordinate(latitude=latitude, longitude=longitude):
                return {
                    "name": place,
                    "latitude": latitude,
                    "longitude": longitude,
                    "resolution_method": "latlon_direct",
                    "geocode_status": "skipped_latlon_available",
                }, {
                    **debug_common,
                    "selected_page_id": page_id,
                    "selected_label": place,
                    "selected_latitude": latitude,
                    "selected_longitude": longitude,
                    "query_status": "ok",
                }
            if place:
                return {
                    "name": place,
                    "latitude": latitude,
                    "longitude": longitude,
                    "resolution_method": "pending_geocode",
                    "geocode_status": "pending",
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
            "effective_time_prop": resolved_time_name,
            "effective_place_prop": resolved_place_name,
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


def _resolve_prop_name(*, env_value: Optional[str], aliases: list[str], schema: dict[str, Any]) -> tuple[Optional[str], dict[str, Any]]:
    if env_value:
        return env_value, {"source": "env", "value": env_value, "resolved": True}
    lower_aliases = {x.lower() for x in aliases}
    for prop_name in schema.keys():
        if prop_name.lower() in lower_aliases:
            return prop_name, {"source": "schema_alias", "value": prop_name, "resolved": True}
    return None, {"source": "schema_alias", "value": None, "resolved": False, "aliases": aliases}


def _fetch_schema(*, token: str, db_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        resp = requests.get(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            return {}, {"ok": False, "status_code": resp.status_code, "reason": "schema_fetch_failed"}
        payload = resp.json()
        props = payload.get("properties") if isinstance(payload, dict) else {}
        return props if isinstance(props, dict) else {}, {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {}, {"ok": False, "reason": "schema_fetch_exception", "error": str(exc)}


def _normalize_place_key(place: str) -> str:
    return " ".join(place.strip().lower().split())


def _load_geocode_cache() -> dict[str, dict[str, float]]:
    try:
        if not GEOCODE_CACHE_PATH.exists():
            return {}
        payload = json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        out: dict[str, dict[str, float]] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            lat = _parse_number({"type": "number", "number": value.get("lat")})
            lon = _parse_number({"type": "number", "number": value.get("lon")})
            if _is_valid_coordinate(latitude=lat, longitude=lon):
                out[key] = {"lat": float(lat), "lon": float(lon)}
        return out
    except Exception:
        return {}


def _save_geocode_cache(cache: dict[str, dict[str, float]]) -> None:
    try:
        GEOCODE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GEOCODE_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logging.warning("weather_geocode_cache_write_failed error=%s", exc)


def _geocode_place(place: str) -> tuple[Optional[float], Optional[float], dict[str, Any]]:
    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": place, "count": 1, "language": "ja", "format": "json"},
            timeout=20,
        )
        if response.status_code >= 400:
            return None, None, {"status": "failed", "reason": f"http_{response.status_code}"}
        results = (response.json() or {}).get("results") or []
        if not results:
            return None, None, {"status": "failed", "reason": "geocoding_no_results"}
        first = results[0]
        lat = _parse_number({"type": "number", "number": first.get("latitude")})
        lon = _parse_number({"type": "number", "number": first.get("longitude")})
        if not _is_valid_coordinate(latitude=lat, longitude=lon):
            return None, None, {"status": "failed", "reason": "invalid_geocode_coordinates"}
        return lat, lon, {"status": "ok", "resolved_name": first.get("name")}
    except Exception as exc:  # noqa: BLE001
        return None, None, {"status": "failed", "reason": f"geocoding_exception:{type(exc).__name__}"}


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
    if resolved.get("resolution_method") == "latlon_direct":
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
    selected_place = _safe_text(resolved.get("name"))
    if selected_place:
        cache = _load_geocode_cache()
        cache_key = _normalize_place_key(selected_place)
        cache_item = cache.get(cache_key)
        if cache_item and _is_valid_coordinate(latitude=cache_item.get("lat"), longitude=cache_item.get("lon")):
            debug["fallback_used"] = "none"
            debug["geocode_status"] = "cache_hit"
            return ResolvedLocation(
                name=selected_place,
                source="location_log_db",
                skip_reason=None,
                debug_summary=debug,
                latitude=cache_item["lat"],
                longitude=cache_item["lon"],
                resolution_method="geocode_cache",
            )
        latitude, longitude, geocode_debug = _geocode_place(selected_place)
        debug["geocode_status"] = geocode_debug.get("status")
        debug["geocode_debug"] = geocode_debug
        if _is_valid_coordinate(latitude=latitude, longitude=longitude):
            cache[cache_key] = {"lat": float(latitude), "lon": float(longitude)}
            _save_geocode_cache(cache)
            debug["fallback_used"] = "none"
            return ResolvedLocation(
                name=selected_place,
                source="location_log_db",
                skip_reason=None,
                debug_summary=debug,
                latitude=latitude,
                longitude=longitude,
                resolution_method="place_geocoding",
            )
        debug["fallback_used"] = "daily_log_or_default_after_geocode_failed"
        debug["reason"] = geocode_debug.get("reason") or "geocoding_failed"
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
