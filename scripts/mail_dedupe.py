from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

UPDATE_SUBJECT_PREFIX = "【更新版】"


@dataclass(frozen=True)
class MailDedupeDecision:
    previous_hash: str | None
    new_hash: str
    hash_changed: bool
    should_send: bool
    is_update_mail: bool
    previous_version: int
    new_version: int

    def apply_subject_prefix(self, subject: str) -> str:
        if not self.is_update_mail:
            return subject
        if subject.startswith(UPDATE_SUBJECT_PREFIX):
            return subject
        return f"{UPDATE_SUBJECT_PREFIX}{subject}"


MAIL_INPUT_HASH_FIELDS = (
    "target_date",
    "diary",
    "today_advice",
    "sleep_analysis_jp",
    "today_condition_forecast_jp",
    "weather_summary",
    "weather_location",
    "weather_temp_max_c",
    "weather_temp_min_c",
    "weather_precip_probability_max",
    "weather_code",
    "activity_summary",
    "location_summary",
    "meal_summary",
    "meal_photos",
    "expenses_total",
    "expenses",
    "done_count",
    "done_tasks",
    "done_tasks_detail",
    "drop_count",
    "drop_tasks",
    "kcal",
    "protein",
    "fat",
    "carb",
    "weight",
    "sleep_start",
    "sleep_end",
    "resolved_sleep_duration_min",
    "resolved_sleep_duration_text",
    "sleep_score",
    "sleep_source",
    "deep_duration_min",
    "rem_duration_min",
    "readiness_stars",
    "readiness_hrv",
    "readiness_bpm",
    "study_minutes",
    "study_sessions",
    "study_last_used_at",
    "f_risk_alert",
    "expense_f_alert",
)


def build_hash_payload(subject: str, body: str) -> str:
    return f"{subject}\n\n{body}"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        as_float = float(value)
        if as_float.is_integer():
            return int(as_float)
        return as_float
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value.keys(), key=lambda k: str(k)):
            normalized_value = _normalize_value(value[key])
            if normalized_value is None:
                continue
            if isinstance(normalized_value, (list, dict)) and not normalized_value:
                continue
            normalized[str(key)] = normalized_value
        return normalized or None
    if isinstance(value, list):
        normalized_list: list[Any] = []
        for item in value:
            normalized_item = _normalize_value(item)
            if normalized_item is None:
                continue
            if isinstance(normalized_item, (list, dict)) and not normalized_item:
                continue
            normalized_list.append(normalized_item)
        return normalized_list or None
    if value is None:
        return None
    return value


def build_mail_input_snapshot(summary: Any, *, expense_f_alert: dict[str, Any], f_risk_alert: dict[str, Any]) -> dict[str, Any]:
    expense_top: list[dict[str, Any]] = []
    expenses = getattr(summary, "expenses", None)
    if expenses and getattr(expenses, "top", None):
        for item in expenses.top:
            expense_top.append(
                {
                    "title": getattr(item, "title", None),
                    "amount": getattr(item, "amount", None),
                    "url": getattr(item, "url", None),
                }
            )

    done_tasks_detail = [
        {
            "title": getattr(task, "title", None),
            "done_date": getattr(task, "done_date", None),
            "event_date": getattr(task, "event_date", None),
        }
        for task in (getattr(summary, "done_tasks_detail", None) or [])
    ]

    raw: dict[str, Any] = {
        "target_date": getattr(summary, "target_date", None),
        "diary": getattr(summary, "diary", None),
        "today_advice": getattr(summary, "today_advice", None),
        "sleep_analysis_jp": getattr(summary, "sleep_analysis_jp", None),
        "today_condition_forecast_jp": getattr(summary, "today_condition_forecast_jp", None),
        "weather_summary": getattr(summary, "weather_summary", None),
        "weather_location": getattr(summary, "weather_location", None),
        "weather_temp_max_c": getattr(summary, "weather_temp_max_c", None),
        "weather_temp_min_c": getattr(summary, "weather_temp_min_c", None),
        "weather_precip_probability_max": getattr(summary, "weather_precip_probability_max", None),
        "weather_code": getattr(summary, "weather_code", None),
        "activity_summary": getattr(summary, "activity_summary", None),
        "location_summary": getattr(summary, "location_summary", None),
        "meal_summary": getattr(summary, "meal_summary", None),
        "meal_photos": getattr(summary, "meal_photos", None),
        "expenses_total": getattr(summary, "expenses_total", None),
        "expenses": {"count": getattr(expenses, "count", None), "top": expense_top},
        "done_count": getattr(summary, "done_count", None),
        "done_tasks": getattr(summary, "done_tasks", None),
        "done_tasks_detail": done_tasks_detail,
        "drop_count": getattr(summary, "drop_count", None),
        "drop_tasks": getattr(summary, "drop_tasks", None),
        "kcal": getattr(summary, "kcal", None),
        "protein": getattr(summary, "protein", None),
        "fat": getattr(summary, "fat", None),
        "carb": getattr(summary, "carb", None),
        "weight": getattr(summary, "weight", None),
        "sleep_start": getattr(summary, "sleep_start", None),
        "sleep_end": getattr(summary, "sleep_end", None),
        "resolved_sleep_duration_min": getattr(summary, "resolved_sleep_duration_min", None),
        "resolved_sleep_duration_text": getattr(summary, "resolved_sleep_duration_text", None),
        "sleep_score": getattr(summary, "sleep_score", None),
        "sleep_source": getattr(summary, "sleep_source", None),
        "deep_duration_min": getattr(summary, "deep_duration_min", None),
        "rem_duration_min": getattr(summary, "rem_duration_min", None),
        "readiness_stars": getattr(summary, "readiness_stars", None),
        "readiness_hrv": getattr(summary, "readiness_hrv", None),
        "readiness_bpm": getattr(summary, "readiness_bpm", None),
        "study_minutes": getattr(summary, "study_minutes", None),
        "study_sessions": getattr(summary, "study_sessions", None),
        "study_last_used_at": getattr(summary, "study_last_used_at", None),
        "f_risk_alert": f_risk_alert.get("summary"),
        "expense_f_alert": expense_f_alert.get("summary"),
    }
    return _normalize_value(raw) or {}


def snapshot_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_version(version: int | None) -> int:
    if not isinstance(version, int):
        return 0
    if version < 0:
        return 0
    return version


def decide_mail_send(
    *,
    subject: str,
    body: str,
    previous_hash: str | None,
    previous_version: int | None,
) -> MailDedupeDecision:
    normalized_previous_hash = (previous_hash or "").strip() or None
    normalized_previous_version = _normalize_version(previous_version)
    new_hash = sha256_hex(build_hash_payload(subject, body))

    if not normalized_previous_hash:
        return MailDedupeDecision(
            previous_hash=None,
            new_hash=new_hash,
            hash_changed=True,
            should_send=True,
            is_update_mail=False,
            previous_version=normalized_previous_version,
            new_version=normalized_previous_version + 1 if normalized_previous_version > 0 else 1,
        )

    if normalized_previous_hash == new_hash:
        return MailDedupeDecision(
            previous_hash=normalized_previous_hash,
            new_hash=new_hash,
            hash_changed=False,
            should_send=False,
            is_update_mail=False,
            previous_version=normalized_previous_version,
            new_version=normalized_previous_version,
        )

    return MailDedupeDecision(
        previous_hash=normalized_previous_hash,
        new_hash=new_hash,
        hash_changed=True,
        should_send=True,
        is_update_mail=True,
        previous_version=normalized_previous_version,
        new_version=max(normalized_previous_version + 1, 2),
    )


def execute_with_update_on_success(
    *,
    decision: MailDedupeDecision,
    send_action: Callable[[], None],
    on_send_success: Callable[[], None],
) -> bool:
    if not decision.should_send:
        return False
    send_action()
    on_send_success()
    return True
