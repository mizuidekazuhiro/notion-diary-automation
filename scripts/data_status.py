"""Source availability is separate from the numeric value in a stored property."""
from __future__ import annotations

import json
from typing import Any

UNAVAILABLE = {"missing", "unknown", "query_failed", "parse_failed", "invalid", "unverified_zero", "stale"}


def source_states(summary: Any) -> dict:
    try:
        result = json.loads(getattr(summary, "data_status_json", None) or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def metric_status(summary: Any, source: str, value: Any) -> str:
    state = source_states(summary).get(source, {})
    status = str(state.get("status") or "") if isinstance(state, dict) else ""
    if status in UNAVAILABLE:
        return status
    if value is None:
        return "missing"
    if value == 0 and source in {"study", "workout"} and status != "confirmed_zero":
        return "unverified_zero"
    return status or "observed"


def metric_available(summary: Any, source: str, value: Any) -> bool:
    return metric_status(summary, source, value) not in UNAVAILABLE


def mask_unavailable_summary(summary: Any) -> Any:
    from dataclasses import is_dataclass, replace
    from copy import copy
    states = source_states(summary)
    changes = {}
    try:
        generation = json.loads(getattr(summary, "generation_metadata", None) or "{}")
        if generation.get("sleep", {}).get("status") == "failed":
            changes.update({name: None for name in ("sleep_analysis_jp", "today_condition_forecast_jp") if hasattr(summary, name)})
    except (ValueError, TypeError, AttributeError):
        pass
    if states.get("sleep", {}).get("status") in UNAVAILABLE:
        for name in ("sleep_start", "sleep_end", "sleep_duration_min", "resolved_sleep_duration_min", "resolved_sleep_duration_hours", "resolved_sleep_duration_text", "sleep_score", "sleep_source", "sleep_analysis_jp", "today_condition_forecast_jp", "sleep_heart_rate", "deep_duration_min", "rem_duration_min", "readiness_stars", "readiness_hrv", "readiness_bpm", "baseline_hrv", "baseline_waking_bpm"):
            if hasattr(summary, name): changes[name] = None
    if states.get("nutrition", {}).get("status") in UNAVAILABLE:
        changes.update({name: None for name in ("kcal", "protein", "fat", "carb", "meal_summary") if hasattr(summary, name)})
    if not changes: return summary
    if is_dataclass(summary): return replace(summary, **changes)
    result = copy(summary)
    for name, value in changes.items(): setattr(result, name, value)
    return result
