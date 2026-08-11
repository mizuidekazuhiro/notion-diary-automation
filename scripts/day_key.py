from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_BOUNDARY_HOUR = 5


def boundary_hour(domain: str = "default") -> int:
    domain_key = f"{domain.upper()}_DAY_BOUNDARY_HOUR"
    raw = os.getenv(domain_key, os.getenv("CANONICAL_DAY_BOUNDARY_HOUR", str(DEFAULT_BOUNDARY_HOUR)))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{domain_key} must be an integer from 0 to 23") from exc
    if value < 0 or value > 23:
        raise ValueError(f"{domain_key} must be an integer from 0 to 23")
    return value


def resolve_day_key(value: datetime, *, domain: str = "default", hour: int | None = None) -> str:
    resolved_hour = boundary_hour(domain) if hour is None else hour
    if resolved_hour < 0 or resolved_hour > 23:
        raise ValueError("boundary hour must be an integer from 0 to 23")
    localized = value.replace(tzinfo=JST) if value.tzinfo is None else value.astimezone(JST)
    return (localized - timedelta(hours=resolved_hour)).date().isoformat()
