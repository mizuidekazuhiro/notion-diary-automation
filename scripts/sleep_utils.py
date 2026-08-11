from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from scripts.day_key import boundary_hour, resolve_day_key

JST = ZoneInfo("Asia/Tokyo")


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


def _safe_score(value: object) -> Optional[float]:
    scored = _safe_float(value)
    if scored is None:
        return None
    return round(scored, 2)


def resolve_sleep_duration_minutes(
    sleep_start: object,
    sleep_end: object,
    raw_sleep_duration_min: object,
) -> SleepDurationResolution:
    raw = _safe_float(raw_sleep_duration_min)
    if raw is not None and raw > 0:
        return SleepDurationResolution(
            resolved_sleep_duration_min=raw,
            duration_source="canonical_raw_duration",
            invalid_reason=None,
        )

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

    if raw is not None and raw <= 0:
        return SleepDurationResolution(
            resolved_sleep_duration_min=None,
            duration_source="canonical_raw_duration",
            invalid_reason="duration_non_positive",
        )

    return SleepDurationResolution(
        resolved_sleep_duration_min=None,
        duration_source="missing",
        invalid_reason="missing_duration",
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
    return resolve_day_key(base, domain="sleep", hour=boundary_hour("sleep"))


def build_sleep_candidate(
    *,
    candidate_date: str,
    source: str,
    sleep_start: object,
    sleep_end: object,
    raw_sleep_duration_min: object,
    sleep_score: object,
) -> dict[str, Any]:
    resolved = resolve_sleep_duration_minutes(sleep_start, sleep_end, raw_sleep_duration_min)
    score = _safe_score(sleep_score)
    resolved_sleep_duration_min = resolved.resolved_sleep_duration_min
    is_valid = bool(
        (resolved_sleep_duration_min is not None and resolved_sleep_duration_min > 0)
        or (score is not None and score > 0)
    )
    return {
        "candidate_date": candidate_date,
        "source": source,
        "sleep_start": sleep_start,
        "sleep_end": sleep_end,
        "raw_sleep_duration_min": _safe_float(raw_sleep_duration_min),
        "resolved_sleep_duration_min": resolved_sleep_duration_min,
        "sleep_score": score,
        "duration_source": resolved.duration_source,
        "candidate_valid_flag": is_valid,
        "invalid_reason": None if is_valid else (resolved.invalid_reason or "missing_sleep_signal"),
        "candidate_target_date": resolve_sleep_target_date(
            sleep_start=sleep_start,
            sleep_end=sleep_end,
            fallback_date=candidate_date,
        ),
        "selection_reason": None,
    }


def resolve_sleep_for_target_date(
    *,
    target_date: str,
    today_summary: Any,
    history_summaries: Sequence[Any],
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]], str]:
    candidates: list[dict[str, Any]] = [
        build_sleep_candidate(
            candidate_date=str(getattr(today_summary, "target_date", target_date)),
            source="today_saved_properties",
            sleep_start=getattr(today_summary, "sleep_start", None),
            sleep_end=getattr(today_summary, "sleep_end", None),
            raw_sleep_duration_min=getattr(today_summary, "sleep_duration_min", None),
            sleep_score=getattr(today_summary, "sleep_score", None),
        )
    ]
    for item in history_summaries:
        candidates.append(
            build_sleep_candidate(
                candidate_date=str(getattr(item, "target_date", "")),
                source="history",
                sleep_start=getattr(item, "sleep_start", None),
                sleep_end=getattr(item, "sleep_end", None),
                raw_sleep_duration_min=getattr(item, "sleep_duration_min", None),
                sleep_score=getattr(item, "sleep_score", None),
            )
        )

    preferred = [c for c in candidates if c.get("source") == "today_saved_properties" and c.get("candidate_valid_flag")]
    if preferred:
        selected = dict(preferred[0])
        selected["selection_reason"] = "use_saved_today_properties"
        return candidates, selected, "saved_today_properties"

    same_target_valid = [c for c in candidates if c.get("candidate_target_date") == target_date and c.get("candidate_valid_flag")]
    if same_target_valid:
        selected = dict(same_target_valid[0])
        selected["selection_reason"] = "match_target_date_with_05_boundary"
        return candidates, selected, "history_target_date_match"

    return candidates, None, "no_valid_candidate"


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
