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
    if not token or not location_log_db_id:
        return {}, {"enabled": False, "reason": "missing_notion_env"}

    date_property_name = (os.getenv("LOCATION_LOG_TIME_PROP", "").strip() or "Time")
    place_label_property_name = (os.getenv("LOCATION_LOG_PLACE_LABEL_PROP", "").strip() or "PlaceLabel")
    place_property_name = (os.getenv("LOCATION_LOG_PLACE_PROP", "").strip() or "Place")
    latitude_property_name = (os.getenv("LOCATION_LOG_LATITUDE_PROP", "").strip() or "Latitude (raw)")
    longitude_property_name = (os.getenv("LOCATION_LOG_LONGITUDE_PROP", "").strip() or "Longitude (raw)")
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
            return {}, {"enabled": True, "reason": f"notion_error_{response.status_code}"}
        data = response.json()
        results = data.get("results", [])
        used_property_names = {
            "time": date_property_name,
            "place_label": place_label_property_name,
            "place": place_property_name,
            "latitude": latitude_property_name,
            "longitude": longitude_property_name,
        }
        missing_property_names: set[str] = set()
        for page in results:
            props = page.get("properties", {})
            page_id = page.get("id")
            place_label_raw = props.get(place_label_property_name)
            place_raw = props.get(place_property_name)
            latitude_raw = props.get(latitude_property_name)
            longitude_raw = props.get(longitude_property_name)
            if place_label_raw is None:
                missing_property_names.add(place_label_property_name)
            if place_raw is None:
                missing_property_names.add(place_property_name)
            if latitude_raw is None:
                missing_property_names.add(latitude_property_name)
            if longitude_raw is None:
                missing_property_names.add(longitude_property_name)
            place_label = _safe_text(_rich_text_plain(place_label_raw))
            place = _safe_text(_rich_text_plain(place_raw))
            latitude = _parse_number(latitude_raw)
            longitude = _parse_number(longitude_raw)
            has_valid_coordinates = _is_valid_coordinate(latitude=latitude, longitude=longitude)
            selected_label = place_label or place
            if has_valid_coordinates and selected_label:
                return {
                    "name": selected_label,
                    "latitude": latitude,
                    "longitude": longitude,
                    "resolution_method": "coordinates_direct",
                }, {
                    "enabled": True,
                    "source": "location_log_db",
                    "candidate_count": len(results),
                    "selected_page_id": page_id,
                    "selected_label": selected_label,
                    "used_property_names": used_property_names,
                    "resolution_method": "coordinates_direct",
                    "coordinates_used": True,
                    "geocoding_used": False,
                }
            if has_valid_coordinates and not selected_label:
                continue
            if latitude is not None or longitude is not None:
                invalid_parts: list[str] = []
                if latitude is None or not (-90 <= latitude <= 90):
                    invalid_parts.append("latitude")
                if longitude is None or not (-180 <= longitude <= 180):
                    invalid_parts.append("longitude")
                if place_label:
                    return {
                        "name": place_label,
                        "latitude": None,
                        "longitude": None,
                        "resolution_method": "place_label_geocoding",
                    }, {
                        "enabled": True,
                        "source": "location_log_db",
                        "candidate_count": len(results),
                        "selected_page_id": page_id,
                        "selected_label": place_label,
                        "used_property_names": used_property_names,
                        "resolution_method": "place_label_geocoding",
                        "coordinates_used": False,
                        "geocoding_used": True,
                        "invalid_coordinates": invalid_parts,
                    }
                if place:
                    return {
                        "name": place,
                        "latitude": None,
                        "longitude": None,
                        "resolution_method": "place_geocoding",
                    }, {
                        "enabled": True,
                        "source": "location_log_db",
                        "candidate_count": len(results),
                        "selected_page_id": page_id,
                        "selected_label": place,
                        "used_property_names": used_property_names,
                        "resolution_method": "place_geocoding",
                        "coordinates_used": False,
                        "geocoding_used": True,
                        "invalid_coordinates": invalid_parts,
                    }
                return {}, {
                    "enabled": True,
                    "source": "location_log_db",
                    "reason": "invalid_coordinates",
                    "candidate_count": len(results),
                    "selected_page_id": page_id,
                    "used_property_names": used_property_names,
                    "coordinates_used": False,
                    "geocoding_used": False,
                    "invalid_coordinates": invalid_parts,
                }
            if place_label:
                return {
                    "name": place_label,
                    "latitude": None,
                    "longitude": None,
                    "resolution_method": "place_label_geocoding",
                }, {
                    "enabled": True,
                    "source": "location_log_db",
                    "candidate_count": len(results),
                    "selected_page_id": page_id,
                    "selected_label": place_label,
                    "used_property_names": used_property_names,
                    "resolution_method": "place_label_geocoding",
                    "coordinates_used": False,
                    "geocoding_used": True,
                }
            if place:
                return {
                    "name": place,
                    "latitude": None,
                    "longitude": None,
                    "resolution_method": "place_geocoding",
                }, {
                    "enabled": True,
                    "source": "location_log_db",
                    "candidate_count": len(results),
                    "selected_page_id": page_id,
                    "selected_label": place,
                    "used_property_names": used_property_names,
                    "resolution_method": "place_geocoding",
                    "coordinates_used": False,
                    "geocoding_used": True,
                }
        reason = "no_usable_row"
        if results and missing_property_names:
            reason = "missing_property"
        return {}, {
            "enabled": True,
            "reason": reason,
            "candidate_count": len(results),
            "used_property_names": used_property_names,
            "missing_property_names": sorted(missing_property_names),
            "coordinates_used": False,
            "geocoding_used": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {}, {"enabled": True, "reason": "exception", "error": str(exc)}


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
        return ResolvedLocation(
            name=resolved.get("name"),
            source="location_log_db",
            skip_reason=None,
            debug_summary=debug,
            latitude=resolved.get("latitude"),
            longitude=resolved.get("longitude"),
            resolution_method=resolved.get("resolution_method"),
        )
    debug["fallback"] = "location_log_db_only"
    logging.info("weather_location_unresolved debug=%s", debug)
    return ResolvedLocation(
        name=None,
        source="location_log_db",
        skip_reason=location_debug.get("reason", "location_log_db_unavailable"),
        debug_summary=debug,
    )
