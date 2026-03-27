from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DAILY_BOUNDARY_HOUR = 5


def _safe_float(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_sleep_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


@dataclass(frozen=True)
class SleepDurationResolution:
    resolved_sleep_duration_min: Optional[float]
    duration_source: str
    invalid_reason: Optional[str]


def resolve_sleep_duration_minutes(
    sleep_start: object,
    sleep_end: object,
    raw_sleep_duration_min: object,
) -> SleepDurationResolution:
    start = parse_sleep_datetime(sleep_start)
    end = parse_sleep_datetime(sleep_end)
    if start and end:
        diff_minutes = (end - start).total_seconds() / 60.0
        if diff_minutes <= 0:
            return SleepDurationResolution(
                resolved_sleep_duration_min=None,
                duration_source="derived_from_start_end",
                invalid_reason="end_before_or_equal_start",
            )
        return SleepDurationResolution(
            resolved_sleep_duration_min=round(diff_minutes, 2),
            duration_source="derived_from_start_end",
            invalid_reason=None,
        )

    raw = _safe_float(raw_sleep_duration_min)
    if raw is None:
        return SleepDurationResolution(
            resolved_sleep_duration_min=None,
            duration_source="missing",
            invalid_reason="missing_duration",
        )
    if raw <= 0:
        return SleepDurationResolution(
            resolved_sleep_duration_min=None,
            duration_source="raw_duration",
            invalid_reason="duration_non_positive",
        )
    return SleepDurationResolution(
        resolved_sleep_duration_min=raw,
        duration_source="raw_duration",
        invalid_reason=None,
    )


def resolve_sleep_target_date(
    *,
    sleep_start: object,
    sleep_end: object,
    fallback_date: Optional[str] = None,
) -> Optional[str]:
    base = parse_sleep_datetime(sleep_start) or parse_sleep_datetime(sleep_end)
    if base is None:
        return fallback_date
    attributed = (base - timedelta(hours=DAILY_BOUNDARY_HOUR)).date()
    return attributed.isoformat()


def format_sleep_duration_text(duration_min: object) -> Optional[str]:
    minutes_float = _safe_float(duration_min)
    if minutes_float is None or minutes_float <= 0:
        return None
    minutes = int(round(minutes_float))
    hours, remain = divmod(minutes, 60)
    if hours <= 0:
        return f"{remain}分"
    return f"{hours}時間{remain}分"
