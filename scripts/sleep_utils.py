from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
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


@dataclass(frozen=True)
class CanonicalSleepMetrics:
    resolved_sleep_duration_min: Optional[float]
    resolved_sleep_duration_hours: Optional[float]
    resolved_sleep_duration_text: Optional[str]
    sleep_duration_source: str
    invalid_reason: Optional[str]


@dataclass(frozen=True)
class SleepTextValidationResult:
    is_consistent: bool
    found_duration_text: Optional[str]
    reason: Optional[str]


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


def resolve_canonical_sleep_metrics(
    sleep_start: object,
    sleep_end: object,
    raw_sleep_duration_min: object,
) -> CanonicalSleepMetrics:
    resolved = resolve_sleep_duration_minutes(sleep_start, sleep_end, raw_sleep_duration_min)
    resolved_min = resolved.resolved_sleep_duration_min
    return CanonicalSleepMetrics(
        resolved_sleep_duration_min=resolved_min,
        resolved_sleep_duration_hours=(round(resolved_min / 60.0, 2) if resolved_min is not None else None),
        resolved_sleep_duration_text=format_sleep_duration_text(resolved_min),
        sleep_duration_source=resolved.duration_source,
        invalid_reason=resolved.invalid_reason,
    )


def build_sleep_duration_text(duration_min: object) -> Optional[str]:
    return format_sleep_duration_text(duration_min)


_DURATION_JA_PATTERN = re.compile(r"(?P<hours>\d{1,2})時間(?P<minutes>\d{1,2})分")


def validate_generated_sleep_text(
    text: object,
    *,
    canonical_sleep_duration_min: object,
    canonical_sleep_duration_text: object,
) -> SleepTextValidationResult:
    canonical_min = _safe_float(canonical_sleep_duration_min)
    canonical_text = str(canonical_sleep_duration_text or "").strip() or None
    body = str(text or "")
    if not body.strip():
        return SleepTextValidationResult(is_consistent=True, found_duration_text=None, reason="empty_text")
    if canonical_min is None or canonical_min <= 0 or not canonical_text:
        return SleepTextValidationResult(is_consistent=True, found_duration_text=None, reason="no_canonical_duration")

    rounded_canonical = int(round(canonical_min))
    for match in _DURATION_JA_PATTERN.finditer(body):
        matched_text = match.group(0)
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes") or 0) + (hours * 60)
        if minutes != rounded_canonical:
            return SleepTextValidationResult(
                is_consistent=False,
                found_duration_text=matched_text,
                reason="duration_text_mismatch",
            )
    return SleepTextValidationResult(is_consistent=True, found_duration_text=None, reason=None)
