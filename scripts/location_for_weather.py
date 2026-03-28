from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
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
    ptype = prop.get("type")
    value: Any = None
    if ptype == "number":
        value = prop.get("number")
    elif ptype == "formula":
        formula = prop.get("formula")
        if isinstance(formula, dict) and formula.get("type") == "number":
            value = formula.get("number")
    elif ptype in {"rich_text", "title", "select"}:
        value = _rich_text_plain(prop)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_valid_coordinate(*, latitude: Optional[float], longitude: Optional[float]) -> bool:
    if latitude is None or longitude is None:
        return False
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


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
            headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
            timeout=20,
        )
        if resp.status_code >= 400:
            return {}, {"ok": False, "status_code": resp.status_code, "reason": "schema_fetch_failed"}
        payload = resp.json()
        props = payload.get("properties") if isinstance(payload, dict) else {}
        return props if isinstance(props, dict) else {}, {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {}, {"ok": False, "reason": "schema_fetch_exception", "error": str(exc)}


def _normalize_geocode_query(place: str) -> str:
    text = place.strip()
    text = re.sub(r"〒\s*\d{3}-?\d{4}", "", text)
    text = re.sub(r"\b(Japan|日本国|日本)\b$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[,、，]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _query_location_log_place(now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    token = os.getenv("NOTION_TOKEN", "").strip()
    location_log_db_id = os.getenv("LOCATION_LOG_DB_ID", "").strip()
    time_prop_env = os.getenv("LOCATION_LOG_TIME_PROP", "").strip() or "Time"
    place_prop_env = os.getenv("LOCATION_LOG_PLACE_PROP", "").strip() or "Place"
    lat_prop_env = os.getenv("LOCATION_LOG_LAT_PROP", "").strip()
    lon_prop_env = os.getenv("LOCATION_LOG_LON_PROP", "").strip()
    base_debug: dict[str, Any] = {
        "effective_time_prop": time_prop_env,
        "effective_place_prop": place_prop_env,
        "resolved_lat_prop": None,
        "resolved_lon_prop": None,
        "latlon_available": False,
        "geocode_attempted": False,
        "geocode_query": None,
        "fallback_used": "none",
    }
    if not token or not location_log_db_id:
        return {}, {
            **base_debug,
            "query_status": "missing_notion_env",
            "weather_status": "skipped",
            "notion_token_present": bool(token),
            "location_log_db_id_present": bool(location_log_db_id),
        }

    schema, schema_debug = _fetch_schema(token=token, db_id=location_log_db_id)
    resolved_time_name, resolved_time_debug = _resolve_prop_name(env_value=time_prop_env, aliases=["Time", "time", "日時", "Date", "date"], schema=schema)
    resolved_place_name, resolved_place_debug = _resolve_prop_name(env_value=place_prop_env, aliases=["Place", "place", "場所", "Location", "location"], schema=schema)

    # fixed property names are highest priority
    resolved_lat_name = "Latitude (raw)" if "Latitude (raw)" in schema else None
    resolved_lon_name = "Longitude (raw)" if "Longitude (raw)" in schema else None
    resolved_lat_debug = {"source": "fixed", "value": resolved_lat_name, "resolved": bool(resolved_lat_name)}
    resolved_lon_debug = {"source": "fixed", "value": resolved_lon_name, "resolved": bool(resolved_lon_name)}
    if not resolved_lat_name:
        resolved_lat_name, resolved_lat_debug = _resolve_prop_name(env_value=lat_prop_env or None, aliases=["latitude", "lat", "Latitude", "Lat", "緯度"], schema=schema)
    if not resolved_lon_name:
        resolved_lon_name, resolved_lon_debug = _resolve_prop_name(env_value=lon_prop_env or None, aliases=["longitude", "lon", "lng", "Longitude", "Lon", "Lng", "経度"], schema=schema)

    common = {
        **base_debug,
        "effective_time_prop": resolved_time_name or time_prop_env,
        "effective_place_prop": resolved_place_name or place_prop_env,
        "resolved_lat_prop": resolved_lat_name,
        "resolved_lon_prop": resolved_lon_name,
        "resolved_props": {
            "time": {"resolved_name": resolved_time_name, **resolved_time_debug},
            "place": {"resolved_name": resolved_place_name, **resolved_place_debug},
            "latitude": {"resolved_name": resolved_lat_name, **resolved_lat_debug},
            "longitude": {"resolved_name": resolved_lon_name, **resolved_lon_debug},
        },
    }
    if not resolved_time_name or not resolved_place_name:
        return {}, {**common, "query_status": "schema_unresolved", "weather_status": "skipped", "schema_fetch": schema_debug}

    payload = {
        "sorts": [{"property": resolved_time_name, "direction": "descending"}],
        "page_size": 1,
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
            return {}, {**common, "query_status": "query_failed", "weather_status": "skipped", "status_code": response.status_code}
        data = response.json()
        results = data.get("results", [])
        if not results:
            return {}, {**common, "query_status": "no_results", "weather_status": "skipped"}

        page = results[0]
        props = page.get("properties", {})
        place = _safe_text(_rich_text_plain(props.get(resolved_place_name)))
        latitude = _parse_number(props.get(resolved_lat_name) if resolved_lat_name else None)
        longitude = _parse_number(props.get(resolved_lon_name) if resolved_lon_name else None)
        latest_time = _safe_text(((props.get(resolved_time_name) or {}).get("date") or {}).get("start"))
        common.update({"latest_selected_page_id": page.get("id"), "latest_selected_time": latest_time})

        if _is_valid_coordinate(latitude=latitude, longitude=longitude):
            return {
                "name": place,
                "latitude": latitude,
                "longitude": longitude,
                "resolution_method": "location_log_latest_latlon",
                "selected_place": place,
            }, {**common, "query_status": "ok", "latlon_available": True, "weather_status": "ok"}

        return {
            "name": place,
            "resolution_method": "location_log_latest_place",
            "selected_place": place,
        }, {**common, "query_status": "ok", "latlon_available": False, "weather_status": "pending"}
    except Exception as exc:  # noqa: BLE001
        return {}, {
            **common,
            "query_status": "query_failed",
            "weather_status": "skipped",
            "query_exception_class": exc.__class__.__name__,
            "query_exception_message": str(exc),
        }


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
    debug: dict[str, Any] = {}
    resolved, location_debug = _query_location_log_place(now)
    debug.update(location_debug)

    if _is_valid_coordinate(latitude=resolved.get("latitude"), longitude=resolved.get("longitude")):
        debug["fallback_used"] = "none"
        debug["geocode_attempted"] = False
        return ResolvedLocation(
            name=resolved.get("name") or resolved.get("selected_place"),
            source="location_log_db_latest",
            skip_reason=None,
            debug_summary=debug,
            latitude=resolved.get("latitude"),
            longitude=resolved.get("longitude"),
            resolution_method=resolved.get("resolution_method"),
        )

    latest_place = _safe_text(resolved.get("selected_place") or resolved.get("name"))
    if latest_place:
        normalized_place = _normalize_geocode_query(latest_place)
        debug["geocode_attempted"] = True
        debug["geocode_query"] = normalized_place
        cache = _load_geocode_cache()
        cache_key = _normalize_place_key(normalized_place)
        cache_item = cache.get(cache_key)
        if cache_item and _is_valid_coordinate(latitude=cache_item.get("lat"), longitude=cache_item.get("lon")):
            debug["fallback_used"] = "location_log_latest_place_geocode_cache"
            return ResolvedLocation(
                name=latest_place,
                source="location_log_db_latest",
                skip_reason=None,
                debug_summary=debug,
                latitude=cache_item["lat"],
                longitude=cache_item["lon"],
                resolution_method="location_log_latest_place_geocode_cache",
            )
        latitude, longitude, geocode_debug = _geocode_place(normalized_place)
        debug["geocode_debug"] = geocode_debug
        if _is_valid_coordinate(latitude=latitude, longitude=longitude):
            cache[cache_key] = {"lat": float(latitude), "lon": float(longitude)}
            _save_geocode_cache(cache)
            debug["fallback_used"] = "location_log_latest_place_geocode"
            return ResolvedLocation(
                name=latest_place,
                source="location_log_db_latest",
                skip_reason=None,
                debug_summary=debug,
                latitude=latitude,
                longitude=longitude,
                resolution_method="location_log_latest_place_geocode",
            )

    place = _safe_text(getattr(summary, "place", None))
    if place:
        debug["fallback_used"] = "daily_log_place"
        return ResolvedLocation(name=place, source="daily_log_place", skip_reason=None, debug_summary=debug, resolution_method="daily_log_place")

    location_summary = _safe_text(getattr(summary, "location_summary", None))
    if location_summary:
        debug["fallback_used"] = "daily_log_location_summary"
        return ResolvedLocation(name=location_summary, source="daily_log_location_summary", skip_reason=None, debug_summary=debug, resolution_method="daily_log_location_summary")

    debug["fallback_used"] = "tokyo_default"
    return ResolvedLocation(name="東京都", source="fallback_default_tokyo", skip_reason=None, debug_summary=debug, resolution_method="tokyo_default")
