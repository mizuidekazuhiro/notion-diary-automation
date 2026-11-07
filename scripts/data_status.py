"""Source availability is separate from the numeric value in a stored property."""
from __future__ import annotations

import json
from typing import Any

UNAVAILABLE = {"missing", "unknown", "query_failed", "parse_failed", "invalid", "unverified_zero"}


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
