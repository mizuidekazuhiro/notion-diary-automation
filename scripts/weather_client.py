from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import requests


@dataclass(frozen=True)
class WeatherResult:
    available: bool
    location_label: str
    summary: Optional[str]
    temp_max_c: Optional[float]
    temp_min_c: Optional[float]
    precip_probability_max: Optional[float]
    weather_code: Optional[int]
    retrieved_at: str
    debug_summary: dict[str, Any]
    skip_reason: Optional[str] = None


WEATHER_CODE_MAP = {
    0: "晴れ",
    1: "概ね晴れ",
    2: "一部くもり",
    3: "くもり",
    45: "霧",
    48: "着氷性の霧",
    51: "弱い霧雨",
    53: "霧雨",
    55: "強い霧雨",
    61: "弱い雨",
    63: "雨",
    65: "強い雨",
    71: "弱い雪",
    73: "雪",
    75: "強い雪",
    80: "弱いにわか雨",
    81: "にわか雨",
    82: "強いにわか雨",
    95: "雷雨",
}


def _to_iso_utc() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def fetch_weather_for_date(
    *,
    location_label: str,
    target_date: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> WeatherResult:
    retrieved_at = _to_iso_utc()
    try:
        lat = latitude
        lon = longitude
        resolved_name = location_label
        used_geocoding = False
        if lat is None or lon is None:
            used_geocoding = True
            geo_resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location_label, "count": 1, "language": "ja", "format": "json"},
                timeout=20,
            )
            if geo_resp.status_code >= 400:
                return WeatherResult(False, location_label, None, None, None, None, None, retrieved_at, {"stage": "geocode", "status": geo_resp.status_code}, "weather_api_failed")
            geo_data = geo_resp.json()
            results = geo_data.get("results") or []
            if not results:
                return WeatherResult(False, location_label, None, None, None, None, None, retrieved_at, {"stage": "geocode", "reason": "no_results"}, "geocoding_no_results")
            first = results[0]
            lat = first.get("latitude")
            lon = first.get("longitude")
            resolved_name = first.get("name") or location_label

        forecast_resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "Asia/Tokyo",
                "start_date": target_date,
                "end_date": target_date,
            },
            timeout=20,
        )
        if forecast_resp.status_code >= 400:
            return WeatherResult(False, resolved_name, None, None, None, None, None, retrieved_at, {"stage": "forecast", "status": forecast_resp.status_code}, "weather_api_failed")
        forecast_data = forecast_resp.json()
        daily = forecast_data.get("daily") or {}
        code = _first_num(daily.get("weathercode"), cast_int=True)
        temp_max = _first_num(daily.get("temperature_2m_max"))
        temp_min = _first_num(daily.get("temperature_2m_min"))
        precip_max = _first_num(daily.get("precipitation_probability_max"))
        summary = WEATHER_CODE_MAP.get(code, "不明") if code is not None else None
        return WeatherResult(
            available=True,
            location_label=str(resolved_name),
            summary=summary,
            temp_max_c=temp_max,
            temp_min_c=temp_min,
            precip_probability_max=precip_max,
            weather_code=code,
            retrieved_at=retrieved_at,
            debug_summary={
                "stage": "ok",
                "lat": lat,
                "lon": lon,
                "resolved_name": resolved_name,
                "used_geocoding": used_geocoding,
                "weather_code": code,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return WeatherResult(False, location_label, None, None, None, None, None, retrieved_at, {"stage": "exception", "error": str(exc)}, "weather_api_failed")


def _first_num(value: Any, *, cast_int: bool = False) -> Optional[float | int]:
    if not isinstance(value, list) or not value:
        return None
    head = value[0]
    try:
        numeric = float(head)
    except (TypeError, ValueError):
        return None
    if cast_int:
        return int(round(numeric))
    return numeric
