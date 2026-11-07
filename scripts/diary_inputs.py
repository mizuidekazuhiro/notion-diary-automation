"""Build source-only diary input and deterministic cache keys, without I/O.

The daily job owns acquisition and persistence. This module owns the exact
field projection, missing-field diagnostics, and raw-input hashing contract.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import NamedTuple

from publish.read_daily_log import DailyLogSummary
from scripts.data_status import metric_status, source_states


class DiaryInputFields(NamedTuple):
    fields: dict[str, str]
    skipped_fields: list[str]
    overview: str
    skip_reasons: dict[str, str]


DIARY_GENERATED_FIELDS = {
    "Today advice", "Sleep Analysis JP", "Today Condition Forecast JP",
}


def _build_done_tasks_detail_text(summary: "DailyLogSummary") -> str:
    if not summary.done_tasks_detail:
        return ""

    parts: list[str] = []
    for task in summary.done_tasks_detail:
        done_date = (task.done_date or "").strip() or "null"
        event_date = (task.event_date or "").strip() or "null"
        parts.append(f"{task.title} | done_date={done_date} | event_date={event_date}")
    return "\n".join(parts)



def build_diary_input_fields(summary: "DailyLogSummary", *, voice_diary_notes_text: str = "") -> DiaryInputFields:
    expenses_details = ""
    if summary.expenses.top:
        parts: list[str] = []
        for item in summary.expenses.top:
            title = item.title.strip() or "Untitled"
            parts.append(f"{title} ({int(item.amount) if float(item.amount).is_integer() else item.amount})")
        if summary.expenses.remaining > 0:
            parts.append(f"ほか{summary.expenses.remaining}件")
        expenses_details = "、".join(parts)

    done_tasks_detail = _build_done_tasks_detail_text(summary)

    canonical_sleep_duration_min = summary.resolved_sleep_duration_min
    canonical_sleep_duration_text = summary.resolved_sleep_duration_text
    candidates = [
        ("Voice Diary Notes", voice_diary_notes_text),
        ("Date", summary.date),
        ("Target Date", summary.target_date_value),
        ("Title", summary.title),
        ("Place", summary.place),
        ("Mood", summary.mood),
        ("Notes", summary.notes),
        ("Done Count", str(summary.done_count) if summary.done_count is not None else None),
        ("Done Tasks", "、".join(summary.done_tasks) if summary.done_tasks else None),
        ("Done Tasks Detail", done_tasks_detail),
        ("Drop Count", str(summary.drop_count) if summary.drop_count is not None else None),
        ("Drop Tasks", "、".join(summary.drop_tasks) if summary.drop_tasks else None),
        (
            "Expenses Total",
            str(int(summary.expenses_total)) if isinstance(summary.expenses_total, (int, float)) and float(summary.expenses_total).is_integer() else str(summary.expenses_total) if summary.expenses_total is not None else None,
        ),
        ("Expenses", expenses_details),
        ("Location summary", summary.location_summary),
        ("Activity Summary", summary.activity_summary),
        ("Meal summary", summary.meal_summary),
        ("Kcal", str(summary.kcal) if summary.kcal is not None else None),
        ("Fat", str(summary.fat) if summary.fat is not None else None),
        ("Carb", str(summary.carb) if summary.carb is not None else None),
        ("Protein", str(summary.protein) if summary.protein is not None else None),
        ("Weight", str(summary.weight) if summary.weight is not None else None),
        ("Sleep Start", summary.sleep_start),
        ("Sleep End", summary.sleep_end),
        ("Sleep Duration", str(canonical_sleep_duration_min) if canonical_sleep_duration_min is not None else None),
        ("Sleep Duration Text", canonical_sleep_duration_text),
        ("Canonical Sleep Duration Min", str(canonical_sleep_duration_min) if canonical_sleep_duration_min is not None else None),
        ("Sleep Duration Source", summary.sleep_duration_source),
        ("Sleep Score", str(summary.sleep_score) if summary.sleep_score is not None else None),
        ("Sleep Source", summary.sleep_source),
        ("Sleep Heart Rate", str(summary.sleep_heart_rate) if summary.sleep_heart_rate is not None else None),
        ("Deep Duration", str(summary.deep_duration_min) if summary.deep_duration_min is not None else None),
        ("REM Duration", str(summary.rem_duration_min) if summary.rem_duration_min is not None else None),
        ("Readiness Stars", str(summary.readiness_stars) if summary.readiness_stars is not None else None),
        ("Readiness HRV", str(summary.readiness_hrv) if summary.readiness_hrv is not None else None),
        ("Readiness BPM", str(summary.readiness_bpm) if summary.readiness_bpm is not None else None),
        ("Baseline HRV", str(summary.baseline_hrv) if summary.baseline_hrv is not None else None),
        ("Baseline Waking BPM", str(summary.baseline_waking_bpm) if summary.baseline_waking_bpm is not None else None),
        ("Study Minutes", str(summary.study_minutes) if summary.study_minutes is not None else None),
        ("Study Sessions", str(summary.study_sessions) if summary.study_sessions is not None else None),
        ("Study Last Used At", summary.study_last_used_at),
        ("Workout Done", "yes" if summary.workout_done else None),
        ("Workout Sessions", str(summary.workout_sessions) if summary.workout_sessions is not None else None),
        ("Workout Gym", summary.workout_gym),
        ("Workout Duration Min", str(summary.workout_duration_min) if summary.workout_duration_min is not None else None),
        ("Workout Sets", str(summary.workout_sets) if summary.workout_sets is not None else None),
        ("Workout Volume Kg", str(summary.workout_volume_kg) if summary.workout_volume_kg is not None else None),
        ("Workout Calories", str(summary.workout_calories) if summary.workout_calories is not None else None),
        ("Workout Exercises", summary.workout_exercises),
        ("Workout Summary", summary.workout_summary),
        ("Data Status", json.dumps({**source_states(summary),
                                   "study": {"status": metric_status(summary, "study", summary.study_minutes)},
                                   "workout": {"status": metric_status(summary, "workout", summary.workout_duration_min)}}, ensure_ascii=False)),
    ]
    if getattr(summary, "location_evidence_json", None):
        try:
            location_evidence = json.loads(summary.location_evidence_json)
            stays = location_evidence.get("main_sessions")
            if isinstance(stays, list) and stays and all(isinstance(stay, dict) and stay.get("start_iso") and stay.get("end_iso") for stay in stays):
                candidates.extend([
                    ("Location Stays", json.dumps(stays, ensure_ascii=False)),
                    ("Location Window", json.dumps(location_evidence.get("window", {}), ensure_ascii=False)),
                ])
        except (ValueError, TypeError, AttributeError):
            logging.warning("diary_location_evidence_invalid target_date=%s", summary.target_date)

    used: dict[str, str] = {}
    skipped: list[str] = []
    skipped_reason_by_field: dict[str, str] = {}
    overview_parts: list[str] = []

    for name, raw in candidates:
        value = (raw or "").strip()
        if not value:
            skipped.append(name)
            skipped_reason_by_field[name] = "empty_or_missing"
            continue
        used[name] = value
        # CI logs keep only field names and lengths.  Diary inputs can contain
        # Notes, addresses, merchant names, and other personal data.
        overview_parts.append(f"{name}({len(value)} chars)")

    return DiaryInputFields(used, skipped, " | ".join(overview_parts), skipped_reason_by_field)



def _normalize_hash_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        numeric = float(value)
        return int(numeric) if numeric.is_integer() else numeric
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, list):
        normalized_items = []
        for item in value:
            normalized_item = _normalize_hash_value(item)
            if normalized_item in (None, [], {}):
                continue
            normalized_items.append(normalized_item)
        return normalized_items
    if isinstance(value, dict):
        normalized_dict: dict[str, object] = {}
        for key in sorted(value.keys()):
            normalized_item = _normalize_hash_value(value[key])
            if normalized_item in (None, [], {}):
                continue
            normalized_dict[str(key)] = normalized_item
        return normalized_dict
    return str(value).strip() or None



def _build_input_hash(payload: dict[str, object]) -> tuple[str, dict[str, object], str]:
    normalized_payload = _normalize_hash_value(payload)
    if not isinstance(normalized_payload, dict):
        normalized_payload = {}
    normalized_json = json.dumps(
        normalized_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized_json.encode("utf-8")).hexdigest(), normalized_payload, normalized_json



def _redacted_hash_summary(normalized_payload: dict[str, object]) -> dict[str, object]:
    return {
        "groups": sorted(normalized_payload.keys()),
        "present_groups": sorted(
            key for key, value in normalized_payload.items() if value not in (None, "", [], {})
        ),
    }



def build_diary_hash_payload(
    summary: "DailyLogSummary",
    diary_input_fields: dict[str, str],
    *,
    generation_policy: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    generated_field_values = {
        "Today advice": summary.today_advice,
        "Sleep Analysis JP": summary.sleep_analysis_jp,
        "Today Condition Forecast JP": summary.today_condition_forecast_jp,
    }
    generated_inputs_excluded = sorted(
        field_name
        for field_name, field_value in generated_field_values.items()
        if (field_value or "").strip()
    )
    hash_payload = {
        "generation_policy": generation_policy,
        "target_date": summary.target_date,
        "diary_input_fields": diary_input_fields,
    }
    debug_summary = {
        "notes_present": bool((summary.notes or "").strip()),
        "done_count": summary.done_count or 0,
        "drop_count": summary.drop_count or 0,
        "expense_count": summary.expenses.count if summary.expenses else 0,
        "expense_top_count": len(summary.expenses.top) if summary.expenses else 0,
        "meal_photo_count": len(summary.meal_photos),
        "used_field_count": len(diary_input_fields),
        "used_fields": sorted(diary_input_fields.keys()),
        "raw_inputs_only": True,
        "generated_inputs_excluded": generated_inputs_excluded,
    }
    return hash_payload, debug_summary



def _assert_diary_input_consistency(diary_input_fields: dict[str, str]) -> None:
    inconsistent_fields = sorted(set(diary_input_fields.keys()) & DIARY_GENERATED_FIELDS)
    if inconsistent_fields:
        raise RuntimeError(
            f"raw_inputs_only violation: generated fields are included in diary inputs: {inconsistent_fields}"
        )
