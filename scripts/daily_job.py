from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingest.ensure_daily_log_page import ensure_daily_log_page
from ingest.http_client import post_json
from ingest.ingest_sources import ingest_sources
from publish.read_daily_log import read_daily_log
from publish.render_mail import render_mail
from publish.send_mail import MailConfig, send_mail
from scripts.diary_generator import generate_diary_from_daily_log
from scripts.expense_f_aggregator import aggregate_daily_expense_f
from scripts.f_risk_state_store import FRiskStateStore
from scripts.f_risk_generator import generate_f_risk
from scripts.location_for_weather import resolve_location_for_weather
from scripts.mood_advice_generator import (
    build_today_advice_generation_context,
    generate_today_advice,
)
from scripts.sleep_condition_generator import (
    load_recent_daily_logs,
    maybe_generate_sleep_insights,
)
from scripts.sleep_utils import resolve_sleep_for_target_date
from scripts.weather_client import fetch_weather_for_date
from scripts.openai_chat_utils import chat_completion
from scripts.note_batch_labeler import (
    build_notes_label_persistence_payload,
    build_notes_label_input_hash,
    has_persisted_note_label,
    label_notes_in_batches,
)

JST = ZoneInfo("Asia/Tokyo")
DIARY_GENERATED_FIELDS = {
    "Today advice",
    "Sleep Analysis JP",
    "Today Condition Forecast JP",
}
WEATHER_REQUIRED_FIELDS = (
    "weather",
    "weather_retrieved_at",
    "weather_generated_at",
    "weather_input_hash",
)
WEATHER_DETAIL_FIELDS = (
    "weather_location",
    "weather_summary",
    "weather_temp_max_c",
    "weather_temp_min_c",
    "weather_precip_probability_max",
    "weather_code",
)
WEATHER_PROVIDER = "open-meteo-jma"


@dataclass(frozen=True)
class Config:
    mail_from: str
    mail_to: List[str]
    gmail_app_password: str
    tasks_closed_url: str
    daily_log_upsert_url: str
    daily_log_ensure_url: str
    health_ingest_url: str
    expenses_ingest_url: str
    daily_log_read_url: str
    diary_generate_url: str
    diary_mark_notified_url: str
    bearer_token: Optional[str]
    openai_model: str


WORKER_EXECUTE_BASE_PATH = "/execute/api/daily_log"
WORKER_ENDPOINTS = {
    "ensure": f"{WORKER_EXECUTE_BASE_PATH}/ensure",
    "ingest_health": f"{WORKER_EXECUTE_BASE_PATH}/ingest_health",
    "ingest_expenses": f"{WORKER_EXECUTE_BASE_PATH}/ingest_expenses",
    "generate_diary": f"{WORKER_EXECUTE_BASE_PATH}/generate_diary",
    "mark_diary_notified": f"{WORKER_EXECUTE_BASE_PATH}/mark_diary_notified",
    "read": "/api/daily_log",
}


def build_worker_url(base_url: str, endpoint_path: str) -> str:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return urljoin(origin, endpoint_path)


def load_config(*, need_mail: bool, need_tasks: bool) -> Config:
    def read_env(name: str, required: bool) -> str:
        value = os.getenv(name, "")
        if required and not value:
            raise RuntimeError(f"Missing env var: {name}")
        return value

    mail_to_raw = read_env("MAIL_TO", need_mail)
    mail_to = [item.strip() for item in mail_to_raw.split(",") if item.strip()]

    daily_log_upsert_url = read_env("DAILY_LOG_UPSERT_URL", True)

    return Config(
        mail_from=read_env("MAIL_FROM", need_mail),
        mail_to=mail_to,
        gmail_app_password=read_env("GMAIL_APP_PASSWORD", need_mail),
        tasks_closed_url=read_env("TASKS_CLOSED_URL", need_tasks),
        daily_log_upsert_url=daily_log_upsert_url,
        daily_log_ensure_url=build_worker_url(
            daily_log_upsert_url, WORKER_ENDPOINTS["ensure"]
        ),
        health_ingest_url=build_worker_url(
            daily_log_upsert_url, WORKER_ENDPOINTS["ingest_health"]
        ),
        expenses_ingest_url=build_worker_url(
            daily_log_upsert_url, WORKER_ENDPOINTS["ingest_expenses"]
        ),
        daily_log_read_url=build_worker_url(daily_log_upsert_url, WORKER_ENDPOINTS["read"]),
        diary_generate_url=build_worker_url(
            daily_log_upsert_url, WORKER_ENDPOINTS["generate_diary"]
        ),
        diary_mark_notified_url=build_worker_url(
            daily_log_upsert_url, WORKER_ENDPOINTS["mark_diary_notified"]
        ),
        bearer_token=os.getenv("WORKERS_BEARER_TOKEN"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    )


def get_target_date(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(JST)
    target_date = (now - timedelta(days=1)).date()
    return target_date.strftime("%Y-%m-%d")


def get_today_advice_target_mode() -> str:
    mode = (os.getenv("TODAY_ADVICE_TARGET_MODE", "YESTERDAY").strip() or "YESTERDAY").upper()
    if mode not in {"YESTERDAY", "TODAY"}:
        raise RuntimeError("TODAY_ADVICE_TARGET_MODE must be YESTERDAY or TODAY")
    return mode


def resolve_target_date(*, explicit_target_date: Optional[str], now: Optional[datetime] = None, phase: Optional[str] = None) -> str:
    if explicit_target_date:
        return explicit_target_date
    now = now or datetime.now(JST)
    if phase in {"notify_diary", "all"} and get_today_advice_target_mode() == "TODAY":
        return now.date().strftime("%Y-%m-%d")
    return get_target_date(now)


def run_ingest(config: Config, target_date: str, run_id: str) -> None:
    title = f"Daily Log｜{target_date}"
    logging.info(
        "Worker endpoint config: daily_log_upsert_url=%s",
        config.daily_log_upsert_url,
    )
    ensure_result = ensure_daily_log_page(
        ensure_url=config.daily_log_ensure_url,
        target_date=target_date,
        title=title,
        source="automation",
        mail_id=run_id,
        bearer_token=config.bearer_token,
    )

    ingest_sources(
        target_date=target_date,
        page_id=ensure_result.page_id,
        tasks_closed_url=config.tasks_closed_url,
        health_ingest_url=config.health_ingest_url,
        expenses_ingest_url=config.expenses_ingest_url,
        daily_log_upsert_url=config.daily_log_upsert_url,
        bearer_token=config.bearer_token,
        run_id=run_id,
        source_label="automation",
    )


def run_publish(config: Config, target_date: str, run_id: str) -> None:
    summary = read_daily_log(
        daily_log_read_url=config.daily_log_read_url,
        target_date=target_date,
        bearer_token=config.bearer_token,
    )
    if not summary:
        logging.info(
            "Daily_Log summary not found; skipping publish phase. target_date(JST)=%s run_id=%s",
            target_date,
            run_id,
        )
        return

    logging.info(
        "Meal section: summary=%s photos=%d",
        "yes" if summary.meal_summary else "no",
        len(summary.meal_photos),
    )

    expense_f_alert = _compute_expense_f_alert(summary=summary, run_id=run_id)
    f_risk_alert = _compute_f_risk_alert_runtime(config, summary=summary, run_id=run_id)
    logging.info(
        "mail_render_context target_date=%s f_risk_section_rendered=%s expense_f_section_rendered=%s",
        summary.target_date,
        bool(f_risk_alert.get("matched")),
        bool(expense_f_alert.get("matched")),
    )
    weather_summary_source = "saved" if (summary.weather_summary or "").strip() else ("fallback_from_raw" if any(value is not None for value in (summary.weather_code, summary.weather_temp_max_c, summary.weather_temp_min_c, summary.weather_precip_probability_max)) else "empty")
    weather_summary_text = (summary.weather_summary or "").strip()
    logging.info(
        "weather_summary_source=%s weather_summary_text=%s",
        weather_summary_source,
        weather_summary_text,
    )
    mail = render_mail(summary, expense_f_alert=expense_f_alert, f_risk_alert=f_risk_alert)
    weather_section_rendered_html = "Weather" in mail.html_body
    weather_section_rendered_text = "Weather" in mail.plain_text
    logging.info(
        "weather_section_rendered_html=%s weather_section_rendered_text=%s",
        weather_section_rendered_html,
        weather_section_rendered_text,
    )
    mail_config = MailConfig(
        mail_from=config.mail_from,
        mail_to=config.mail_to,
        gmail_app_password=config.gmail_app_password,
    )
    send_mail(mail_config, mail.subject, mail.plain_text, mail.html_body)


def _build_done_tasks_detail_text(summary: "DailyLogSummary") -> str:
    if not summary.done_tasks_detail:
        return ""

    parts: list[str] = []
    for task in summary.done_tasks_detail:
        done_date = (task.done_date or "").strip() or "null"
        event_date = (task.event_date or "").strip() or "null"
        parts.append(f"{task.title} | done_date={done_date} | event_date={event_date}")
    return "\n".join(parts)


def build_diary_input_fields(summary: "DailyLogSummary") -> tuple[dict[str, str], list[str], str, dict[str, str]]:
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
    ]

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
        preview = value.replace("\n", " ")
        if len(preview) > 80:
            preview = f"{preview[:80]}..."
        overview_parts.append(f"{name}({len(value)} chars): {preview}")

    return used, skipped, " | ".join(overview_parts), skipped_reason_by_field


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


def _utc_timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _build_diary_hash_payload(
    summary: "DailyLogSummary",
    diary_input_fields: dict[str, str],
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


def _refresh_daily_log_summary(config: Config, target_date: str) -> Optional["DailyLogSummary"]:
    return read_daily_log(
        daily_log_read_url=config.daily_log_read_url,
        target_date=target_date,
        bearer_token=config.bearer_token,
    )


def _save_daily_log_fields(
    config: Config,
    *,
    target_date: str,
    payload: dict[str, object],
) -> dict:
    return post_json(
        config.diary_generate_url,
        {"target_date": target_date, **payload},
        config.bearer_token,
    )


def _normalize_iso_datetime_for_compare(value: object, *, precision: str = "second") -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return raw
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo("UTC"))
    if precision == "minute":
        parsed = parsed.replace(second=0, microsecond=0)
    else:
        parsed = parsed.replace(microsecond=0)
    return parsed.isoformat()


def _is_non_empty_weather_value(*, field: str, value: object) -> bool:
    if field in {"weather_temp_max_c", "weather_temp_min_c", "weather_precip_probability_max", "weather_code"}:
        return value is not None
    return str(value or "").strip() != ""


def _weather_field_values_equal(*, field: str, expected: object, actual: object) -> bool:
    if field in {"weather_retrieved_at", "weather_generated_at"}:
        return _normalize_iso_datetime_for_compare(expected, precision="minute") == _normalize_iso_datetime_for_compare(actual, precision="minute")
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        try:
            return float(expected) == float(actual)
        except (TypeError, ValueError):
            return False
    return str(expected or "").strip() == str(actual or "").strip()


def _weather_roundtrip_status(*, summary: Optional["DailyLogSummary"], expected_payload: dict[str, object]) -> dict[str, object]:
    if summary is None:
        return {
            "readback_ok": False,
            "compare_ok": False,
            "missing_fields": ["summary_unavailable"],
            "mismatch_fields": [],
            "normalized_save_timestamps": {},
            "normalized_read_timestamps": {},
        }
    missing_fields: list[str] = []
    mismatch_fields: list[str] = []
    actual_by_field: dict[str, object] = {
        "weather": (summary.weather_summary or "").strip(),
        "weather_summary": (summary.weather_summary or "").strip(),
        "weather_location": (summary.weather_location or "").strip(),
        "weather_retrieved_at": (summary.weather_retrieved_at or "").strip(),
        "weather_input_hash": (summary.weather_input_hash or "").strip(),
        "weather_generated_at": (summary.weather_generated_at or "").strip(),
        "weather_temp_max_c": summary.weather_temp_max_c,
        "weather_temp_min_c": summary.weather_temp_min_c,
        "weather_precip_probability_max": summary.weather_precip_probability_max,
        "weather_code": summary.weather_code,
    }
    normalized_save_timestamps = {
        key: _normalize_iso_datetime_for_compare(expected_payload.get(key), precision="minute")
        for key in ("weather_retrieved_at", "weather_generated_at")
    }
    normalized_read_timestamps = {
        key: _normalize_iso_datetime_for_compare(actual_by_field.get(key), precision="minute")
        for key in ("weather_retrieved_at", "weather_generated_at")
    }
    raw_save_timestamps = {
        key: _normalize_iso_datetime_for_compare(expected_payload.get(key), precision="second")
        for key in ("weather_retrieved_at", "weather_generated_at")
    }
    raw_read_timestamps = {
        key: _normalize_iso_datetime_for_compare(actual_by_field.get(key), precision="second")
        for key in ("weather_retrieved_at", "weather_generated_at")
    }
    compare_normalized = normalized_save_timestamps != raw_save_timestamps or normalized_read_timestamps != raw_read_timestamps
    ignored_fields: list[str] = []
    for field, expected in expected_payload.items():
        actual = actual_by_field.get(field)
        if _is_non_empty_weather_value(field=field, value=expected) and not _is_non_empty_weather_value(field=field, value=actual):
            missing_fields.append(field)
            continue
        if not _weather_field_values_equal(field=field, expected=expected, actual=actual):
            mismatch_fields.append(field)
        elif field in {"weather_retrieved_at", "weather_generated_at"} and raw_save_timestamps.get(field) != raw_read_timestamps.get(field):
            ignored_fields.append(field)
    readback_ok = len(missing_fields) == 0
    compare_ok = len(mismatch_fields) == 0
    return {
        "readback_ok": readback_ok,
        "compare_ok": compare_ok,
        "missing_fields": missing_fields,
        "mismatch_fields": mismatch_fields,
        "normalized_save_timestamps": normalized_save_timestamps,
        "normalized_read_timestamps": normalized_read_timestamps,
        "compare_normalized": compare_normalized,
        "ignored_fields": ignored_fields,
    }


def _generate_and_save_sleep_insights(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> "DailyLogSummary":
    history_summaries = load_recent_daily_logs(
        daily_log_read_url=config.daily_log_read_url,
        bearer_token=config.bearer_token,
        target_date=summary.target_date,
        days=7,
    )
    sleep_signal_fields = {
        "sleep_start": summary.sleep_start,
        "sleep_end": summary.sleep_end,
        "sleep_duration_min": summary.sleep_duration_min,
        "sleep_score": summary.sleep_score,
        "sleep_source": summary.sleep_source,
        "sleep_heart_rate": summary.sleep_heart_rate,
        "deep_duration_min": summary.deep_duration_min,
        "rem_duration_min": summary.rem_duration_min,
        "readiness_stars": summary.readiness_stars,
        "readiness_hrv": summary.readiness_hrv,
        "readiness_bpm": summary.readiness_bpm,
        "baseline_hrv": summary.baseline_hrv,
        "baseline_waking_bpm": summary.baseline_waking_bpm,
    }
    available_sleep_inputs = sorted(name for name, value in sleep_signal_fields.items() if value not in (None, ""))
    logging.info("phase_c_sleep_start target_date(JST)=%s run_id=%s", summary.target_date, run_id)
    logging.info(
        "phase_c_sleep_input_summary target_date(JST)=%s run_id=%s available_sleep_input_count=%s available_sleep_inputs=%s existing_sleep_properties=%s",
        summary.target_date,
        run_id,
        len(available_sleep_inputs),
        available_sleep_inputs,
        sorted(
            key
            for key, value in {
                "sleep_analysis_jp": summary.sleep_analysis_jp,
                "today_condition_forecast_jp": summary.today_condition_forecast_jp,
            }.items()
            if (value or "").strip()
        ),
    )
    sleep_candidates, selected_sleep, sleep_selection_mode = resolve_sleep_for_target_date(
        target_date=summary.target_date,
        today_summary=summary,
        history_summaries=history_summaries,
    )
    logging.info(
        "phase_c_sleep_resolver target_date(JST)=%s run_id=%s candidate_count=%s selected=%s selection_mode=%s selection_reason=%s candidate_target_date=%s invalid_reason=%s",
        summary.target_date,
        run_id,
        len(sleep_candidates),
        bool(selected_sleep),
        sleep_selection_mode,
        (selected_sleep or {}).get("selection_reason"),
        (selected_sleep or {}).get("candidate_target_date"),
        (selected_sleep or {}).get("invalid_reason"),
    )
    if not selected_sleep:
        fallback_payload = {
            "sleep_analysis_jp": "睡眠データが不足しているため分析をスキップしました",
            "today_condition_forecast_jp": "",
        }
        logging.info(
            "phase_c_sleep_skipped target_date(JST)=%s run_id=%s skip_reason=missing_required_sleep_data",
            summary.target_date,
            run_id,
        )
        save_result = _save_daily_log_fields(
            config,
            target_date=summary.target_date,
            payload=fallback_payload,
        )
        logging.info(
            "phase_c_sleep_saved target_date(JST)=%s run_id=%s updated=%s reason=%s mode=fixed_fallback generated_properties=%s",
            summary.target_date,
            run_id,
            save_result.get("updated"),
            save_result.get("reason"),
            sorted(k for k, v in fallback_payload.items() if v),
        )
        refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
        return refreshed_summary or summary

    resolver_applied_summary = dataclasses.replace(
        summary,
        sleep_start=selected_sleep.get("sleep_start"),
        sleep_end=selected_sleep.get("sleep_end"),
        sleep_duration_min=selected_sleep.get("raw_sleep_duration_min"),
        resolved_sleep_duration_min=selected_sleep.get("resolved_sleep_duration_min"),
        resolved_sleep_duration_hours=(
            round(float(selected_sleep.get("resolved_sleep_duration_min")) / 60.0, 2)
            if selected_sleep.get("resolved_sleep_duration_min") is not None
            else None
        ),
        sleep_score=selected_sleep.get("sleep_score"),
        sleep_duration_source=str(selected_sleep.get("duration_source") or "missing"),
    )
    sleep_payload = maybe_generate_sleep_insights(
        target_date=summary.target_date,
        today_summary=resolver_applied_summary,
        history_summaries=history_summaries,
    )
    if not sleep_payload:
        logging.info(
            "phase_c_sleep_skipped target_date(JST)=%s run_id=%s skip_reason=no_sleep_signal generated_properties=[]",
            summary.target_date,
            run_id,
        )
        logging.info(
            "phase_c_sleep_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=no_sleep_signal generated_properties=[]",
            summary.target_date,
            run_id,
            False,
        )
        refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
        return refreshed_summary or summary

    logging.info(
        "phase_c_sleep_generated target_date(JST)=%s run_id=%s generated_properties=%s",
        summary.target_date,
        run_id,
        sorted(sleep_payload.keys()),
    )
    save_result = _save_daily_log_fields(config, target_date=summary.target_date, payload=sleep_payload)
    logging.info(
        "phase_c_sleep_saved target_date(JST)=%s run_id=%s updated=%s reason=%s generated_properties=%s",
        summary.target_date,
        run_id,
        save_result.get("updated"),
        save_result.get("reason"),
        sorted(sleep_payload.keys()),
    )
    refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
    return refreshed_summary or summary


def _generate_and_save_today_advice(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> "DailyLogSummary":
    logging.info("phase_c_today_advice_start target_date(JST)=%s run_id=%s", summary.target_date, run_id)
    context = build_today_advice_generation_context(
        daily_log_read_url=config.daily_log_read_url,
        bearer_token=config.bearer_token,
        target_date=summary.target_date,
    )
    if not context:
        logging.info(
            "phase_c_today_advice_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=no_daily_log generated_properties=[]",
            summary.target_date,
            run_id,
            False,
        )
        refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
        return refreshed_summary or summary

    today_state = context["today_state"]
    structured = context["structured"]
    today_advice_hash_payload = {
        "judgment_input": context["judgment_input"],
        "today_facts": {
            "today_sleep": today_state.get("today_sleep", {}),
            "historical_behavior_patterns": today_state.get("historical_behavior_patterns", {}),
            "historical_recording_patterns": today_state.get("historical_recording_patterns", {}),
            "historical_context": today_state.get("historical_context", {}),
        },
    }
    current_input_hash, normalized_hash_payload, _ = _build_input_hash(today_advice_hash_payload)
    previous_input_hash = (summary.today_advice_input_hash or "").strip() or None
    has_today_advice = bool((summary.today_advice or "").strip())
    input_changed = current_input_hash != previous_input_hash
    debug_summary = {
        "current_input_hash": current_input_hash,
        "previous_input_hash": previous_input_hash,
        "input_hash_changed": input_changed,
        "has_previous_input_hash": previous_input_hash is not None,
        "has_today_advice": has_today_advice,
        "sample_days": structured["counts"].get("last_30_days_count"),
        "high_samples": structured.get("high_mood_sample_count"),
        "low_samples": structured.get("low_mood_sample_count"),
        "today_sleep_fields": sorted(today_state.get("today_sleep", {}).keys()),
        "historical_behavior_fields": sorted(today_state.get("historical_behavior_patterns", {}).keys()),
        "historical_recording_fields": sorted(today_state.get("historical_recording_patterns", {}).keys()),
        "expense_count": summary.expenses.count if summary.expenses else 0,
        "hash_input_summary": normalized_hash_payload,
    }
    logging.info(
        "phase_c_today_advice_input_summary target_date(JST)=%s run_id=%s has_today_advice=%s has_notes=%s has_location_summary=%s has_diary=%s debug_summary=%s",
        summary.target_date,
        run_id,
        has_today_advice,
        bool((summary.notes or "").strip()),
        bool((summary.location_summary or "").strip()),
        bool((summary.diary or "").strip()),
        json.dumps(debug_summary, ensure_ascii=False, sort_keys=True, default=str),
    )
    if has_today_advice and not input_changed:
        logging.info(
            "phase_c_today_advice_skip target_date(JST)=%s run_id=%s skip_reason=unchanged_input current_input_hash=%s previous_input_hash=%s input_hash_changed=%s input_summary=%s",
            summary.target_date,
            run_id,
            current_input_hash,
            previous_input_hash,
            input_changed,
            json.dumps(
                {
                    "sample_days": structured["counts"].get("last_30_days_count"),
                    "high_samples": structured.get("high_mood_sample_count"),
                    "low_samples": structured.get("low_mood_sample_count"),
                    "today_sleep_fields": sorted(today_state.get("today_sleep", {}).keys()),
                    "expense_count": summary.expenses.count if summary.expenses else 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        logging.info(
            "phase_c_today_advice_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=unchanged_input generated_properties=[]",
            summary.target_date,
            run_id,
            False,
        )
        refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
        return refreshed_summary or summary

    advice_result = generate_today_advice(
        daily_log_read_url=config.daily_log_read_url,
        bearer_token=config.bearer_token,
        target_date=summary.target_date,
    )
    if not advice_result or not advice_result.today_advice.strip():
        logging.info(
            "phase_c_today_advice_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=no_daily_log generated_properties=[]",
            summary.target_date,
            run_id,
            False,
        )
        refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
        return refreshed_summary or summary

    logging.info(
        "phase_c_today_advice_generated target_date(JST)=%s run_id=%s generated_properties=%s history_days=%s high_samples=%s low_samples=%s evidence_used=%s",
        summary.target_date,
        run_id,
        ["today_advice"],
        advice_result.history_count,
        advice_result.high_mood_sample_count,
        advice_result.low_mood_sample_count,
        advice_result.judgment_json.get("evidence_used", []),
    )
    save_result = _save_daily_log_fields(
        config,
        target_date=summary.target_date,
        payload={
            "today_advice": advice_result.today_advice,
            "today_advice_input_hash": current_input_hash,
            "today_advice_generated_at": _utc_timestamp(),
        },
    )
    logging.info(
        "phase_c_today_advice_saved target_date(JST)=%s run_id=%s updated=%s reason=%s generated_properties=%s current_input_hash=%s previous_input_hash=%s input_hash_changed=%s",
        summary.target_date,
        run_id,
        save_result.get("updated"),
        save_result.get("reason"),
        ["today_advice"],
        current_input_hash,
        previous_input_hash,
        input_changed,
    )
    refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
    return refreshed_summary or summary


def _ensure_notes_label_persisted(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> "DailyLogSummary":
    notes_text = (summary.notes or "").strip()
    expected_hash = build_notes_label_input_hash(notes_text)
    persisted_hash = str(summary.notes_label_input_hash or "").strip()
    if notes_text and has_persisted_note_label(summary) and expected_hash == persisted_hash:
        logging.info(
            "phase_c_notes_label_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=matched_hash",
            summary.target_date,
            run_id,
            False,
        )
        return summary
    note_audit: dict[str, Any] = {}
    labels = label_notes_in_batches(
        summaries=[summary],
        chat_completion=chat_completion,
        model=config.openai_model,
        audit=note_audit,
    )
    label = labels.get(summary.target_date)
    if label is None:
        logging.info(
            "phase_c_notes_label_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=no_label_generated",
            summary.target_date,
            run_id,
            False,
        )
        return summary
    payload = build_notes_label_persistence_payload(summary=summary, label=label, model=config.openai_model)
    save_result = _save_daily_log_fields(config, target_date=summary.target_date, payload=payload)
    logging.info(
        "phase_c_notes_label_saved target_date(JST)=%s run_id=%s updated=%s reason=%s persisted_hit_count=%s",
        summary.target_date,
        run_id,
        save_result.get("updated"),
        save_result.get("reason"),
        note_audit.get("persisted_hit_count", 0),
    )
    refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
    return refreshed_summary or summary


def _generate_and_save_weather(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> "DailyLogSummary":
    weather_forecast_date_jst = datetime.now(JST).date().strftime("%Y-%m-%d")
    logging.info(
        "phase_c_weather_start daily_log_target_date(JST)=%s weather_forecast_date_jst=%s run_id=%s",
        summary.target_date,
        weather_forecast_date_jst,
        run_id,
    )
    resolved_location = resolve_location_for_weather(summary=summary)
    if not resolved_location.name:
        skip_reason = resolved_location.skip_reason or "missing_location_log_db"
        save_result = _save_daily_log_fields(
            config,
            target_date=summary.target_date,
            payload={"weather": "", "weather_generated_at": _utc_timestamp()},
        )
        logging.info(
            "[Weather] source=%s selected_location=%s resolution_method=%s geocode_status=skipped weather_status=location_resolution_failed latlon_available=%s query_status=%s latest_selected_page_id=%s latest_selected_time=%s effective_time_prop=%s effective_place_prop=%s resolved_lat_prop=%s resolved_lon_prop=%s geocode_attempted=%s geocode_query=%s fallback_used=%s saved_to=Weather daily_log_target_date=%s weather_forecast_date_jst=%s updated=%s weather_fetch_ok=%s weather_save_attempted=%s weather_save_ok=%s weather_readback_ok=%s weather_compare_ok=%s weather_readback_missing_fields=%s weather_compare_mismatch_fields=%s weather_timestamp_normalized_save=%s weather_timestamp_normalized_read=%s empty_update_reason=%s weather_retrieved_at=%s location_source=%s debug=%s",
            resolved_location.source,
            "",
            resolved_location.resolution_method,
            bool(resolved_location.latitude is not None and resolved_location.longitude is not None),
            resolved_location.debug_summary.get("query_status"),
            resolved_location.debug_summary.get("latest_selected_page_id"),
            resolved_location.debug_summary.get("latest_selected_time"),
            resolved_location.debug_summary.get("effective_time_prop"),
            resolved_location.debug_summary.get("effective_place_prop"),
            resolved_location.debug_summary.get("resolved_lat_prop"),
            resolved_location.debug_summary.get("resolved_lon_prop"),
            resolved_location.debug_summary.get("geocode_attempted"),
            resolved_location.debug_summary.get("geocode_query"),
            resolved_location.debug_summary.get("fallback_used"),
            summary.target_date,
            weather_forecast_date_jst,
            save_result.get("updated"),
            False,
            True,
            bool(save_result.get("updated")),
            False,
            False,
            ["weather", "weather_retrieved_at", "weather_generated_at", "weather_input_hash"],
            [],
            {},
            {},
            f"location_resolution_failed:{skip_reason}",
            "",
            resolved_location.source,
            json.dumps(resolved_location.debug_summary, ensure_ascii=False, sort_keys=True, default=str),
        )
        return _refresh_daily_log_summary(config, summary.target_date) or summary

    weather_hash_payload = {
        "daily_log_target_date": summary.target_date,
        "weather_forecast_date_jst": weather_forecast_date_jst,
        "location_name": resolved_location.name,
        "location_latitude": resolved_location.latitude,
        "location_longitude": resolved_location.longitude,
        "location_resolution_method": resolved_location.resolution_method,
        "location_source": resolved_location.source,
        "weather_provider": WEATHER_PROVIDER,
    }
    current_input_hash, normalized_hash_payload, _ = _build_input_hash(weather_hash_payload)
    previous_input_hash = (summary.weather_input_hash or "").strip() or None
    has_weather = bool((summary.weather_summary or "").strip())
    input_changed = current_input_hash != previous_input_hash
    logging.info(
        "phase_c_weather_input_summary daily_log_target_date(JST)=%s weather_forecast_date_jst=%s run_id=%s has_weather=%s location=%s lat=%s lon=%s resolution_method=%s location_source=%s debug_summary=%s",
        summary.target_date,
        weather_forecast_date_jst,
        run_id,
        has_weather,
        resolved_location.name,
        resolved_location.latitude,
        resolved_location.longitude,
        resolved_location.resolution_method,
        resolved_location.source,
        json.dumps(
            {
                "current_input_hash": current_input_hash,
                "previous_input_hash": previous_input_hash,
                "input_hash_changed": input_changed,
                "hash_input_summary": normalized_hash_payload,
                "location_debug": resolved_location.debug_summary,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    )
    if has_weather and not input_changed:
        logging.info(
            "phase_c_weather_skip daily_log_target_date(JST)=%s weather_forecast_date_jst=%s run_id=%s skip_reason=unchanged_input unchanged_input_skip=true",
            summary.target_date,
            weather_forecast_date_jst,
            run_id,
        )
        logging.info(
            "phase_c_weather_saved daily_log_target_date(JST)=%s weather_forecast_date_jst=%s run_id=%s updated=%s skip_reason=unchanged_input generated_properties=[]",
            summary.target_date,
            weather_forecast_date_jst,
            run_id,
            False,
        )
        refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
        return refreshed_summary or summary

    weather = fetch_weather_for_date(
        location_label=resolved_location.name or "",
        target_date=weather_forecast_date_jst,
        latitude=resolved_location.latitude,
        longitude=resolved_location.longitude,
    )
    if not weather.available:
        reason = (
            weather.debug_summary.get("reason")
            or (resolved_location.debug_summary.get("geocode_debug") or {}).get("reason")
            or weather.skip_reason
            or "weather_api_failed"
        )
        debug_payload = dict(weather.debug_summary)
        debug_payload["reason"] = reason
        debug_payload["resolution_method"] = resolved_location.resolution_method
        debug_payload["location_source"] = resolved_location.source
        save_result = _save_daily_log_fields(
            config,
            target_date=summary.target_date,
            payload={"weather": "", "weather_generated_at": _utc_timestamp()},
        )
        logging.info(
            "[Weather] source=%s selected_location=%s resolution_method=%s geocode_status=%s weather_status=failed latlon_available=%s saved_to=Weather daily_log_target_date=%s weather_forecast_date_jst=%s updated=%s weather_fetch_ok=%s weather_save_attempted=%s weather_save_ok=%s weather_readback_ok=%s weather_compare_ok=%s weather_readback_missing_fields=%s weather_compare_mismatch_fields=%s weather_timestamp_normalized_save=%s weather_timestamp_normalized_read=%s empty_update_reason=%s weather_retrieved_at=%s location_source=%s api_endpoint=%s requested_daily_fields=%s returned_daily_keys=%s weather_code=%s temp_max=%s temp_min=%s precipitation_sum=%s save_result=%s debug=%s",
            resolved_location.source,
            resolved_location.name,
            resolved_location.resolution_method,
            (resolved_location.debug_summary.get("geocode_debug") or {}).get("status") or weather.debug_summary.get("stage"),
            bool(resolved_location.latitude is not None and resolved_location.longitude is not None),
            summary.target_date,
            weather_forecast_date_jst,
            save_result.get("updated"),
            False,
            True,
            bool(save_result.get("updated")),
            False,
            False,
            ["weather", "weather_retrieved_at", "weather_generated_at", "weather_input_hash"],
            [],
            {},
            {},
            f"weather_fetch_failed:{reason}",
            "",
            resolved_location.source,
            weather.debug_summary.get("api_endpoint"),
            weather.debug_summary.get("requested_daily_fields"),
            weather.debug_summary.get("returned_daily_keys"),
            weather.weather_code,
            weather.temp_max_c,
            weather.temp_min_c,
            weather.precipitation_sum_mm,
            save_result.get("reason"),
            json.dumps(debug_payload, ensure_ascii=False, sort_keys=True, default=str),
        )
        return _refresh_daily_log_summary(config, summary.target_date) or summary

    payload = {
        "weather": weather.summary or "",
        "weather_summary": weather.summary or "",
        "weather_location": weather.location_label or resolved_location.name or "",
        "weather_temp_max_c": weather.temp_max_c,
        "weather_temp_min_c": weather.temp_min_c,
        "weather_precip_probability_max": None,
        "weather_code": weather.weather_code,
        "weather_retrieved_at": weather.retrieved_at,
        "weather_input_hash": current_input_hash,
        "weather_generated_at": _utc_timestamp(),
    }
    logging.info(
        "weather_summary_generated=%s daily_log_target_date=%s weather_forecast_date_jst=%s weather_retrieved_at=%s location_source=%s resolution_method=%s precipitation_sum=%s weather_summary_text=%s",
        bool(weather.summary),
        summary.target_date,
        weather_forecast_date_jst,
        weather.retrieved_at,
        resolved_location.source,
        resolved_location.resolution_method,
        weather.precipitation_sum_mm,
        weather.summary or "",
    )
    weather_save_attempted = True
    save_result = _save_daily_log_fields(config, target_date=summary.target_date, payload=payload)
    weather_save_ok = bool(save_result.get("updated")) and str(save_result.get("reason") or "") in {"updated", ""}
    refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
    roundtrip_status = _weather_roundtrip_status(summary=refreshed_summary, expected_payload=payload)
    readback_ok = bool(roundtrip_status["readback_ok"])
    compare_ok = bool(roundtrip_status["compare_ok"])
    readback_failures = list(roundtrip_status["missing_fields"])
    compare_mismatch_fields = list(roundtrip_status["mismatch_fields"])
    stage_status = "ok" if readback_ok and compare_ok else "weather_readback_or_compare_failed"
    logging.info(
        "[Weather] source=%s selected_location=%s resolution_method=%s geocode_status=%s weather_status=%s latlon_available=%s query_status=%s latest_selected_page_id=%s latest_selected_time=%s effective_time_prop=%s effective_place_prop=%s resolved_lat_prop=%s resolved_lon_prop=%s geocode_attempted=%s geocode_query=%s fallback_used=%s saved_to=Weather daily_log_target_date=%s weather_forecast_date_jst=%s weather_retrieved_at=%s location_source=%s api_endpoint=%s requested_daily_fields=%s returned_daily_keys=%s weather_code=%s temp_max=%s temp_min=%s precipitation_sum=%s updated=%s weather_fetch_ok=%s weather_save_attempted=%s weather_save_ok=%s weather_readback_ok=%s weather_compare_ok=%s weather_readback_missing_fields=%s weather_compare_mismatch_fields=%s weather_timestamp_normalized_save=%s weather_timestamp_normalized_read=%s weather_compare_normalized=%s weather_compare_ignored_fields=%s weather_summary_source=%s weather_summary_text=%s empty_update_reason=%s save_result=%s debug=%s",
        resolved_location.source,
        weather.location_label,
        resolved_location.resolution_method,
        (resolved_location.debug_summary.get("geocode_debug") or {}).get("status")
        or ("skipped_latlon_available" if resolved_location.resolution_method == "latlon_direct" else "ok"),
        stage_status,
        bool(resolved_location.latitude is not None and resolved_location.longitude is not None),
        resolved_location.debug_summary.get("query_status"),
        resolved_location.debug_summary.get("latest_selected_page_id"),
        resolved_location.debug_summary.get("latest_selected_time"),
        resolved_location.debug_summary.get("effective_time_prop"),
        resolved_location.debug_summary.get("effective_place_prop"),
        resolved_location.debug_summary.get("resolved_lat_prop"),
        resolved_location.debug_summary.get("resolved_lon_prop"),
        resolved_location.debug_summary.get("geocode_attempted"),
        resolved_location.debug_summary.get("geocode_query"),
        resolved_location.debug_summary.get("fallback_used"),
        summary.target_date,
        weather_forecast_date_jst,
        weather.retrieved_at,
        resolved_location.source,
        weather.debug_summary.get("api_endpoint"),
        weather.debug_summary.get("requested_daily_fields"),
        weather.debug_summary.get("returned_daily_keys"),
        weather.weather_code,
        weather.temp_max_c,
        weather.temp_min_c,
        weather.precipitation_sum_mm,
        save_result.get("updated"),
        weather.available,
        weather_save_attempted,
        weather_save_ok,
        readback_ok,
        compare_ok,
        readback_failures,
        compare_mismatch_fields,
        roundtrip_status["normalized_save_timestamps"],
        roundtrip_status["normalized_read_timestamps"],
        roundtrip_status["compare_normalized"],
        roundtrip_status["ignored_fields"],
        "saved" if (weather.summary or "").strip() else "empty",
        weather.summary or "",
        "",
        save_result.get("reason"),
        json.dumps({**weather.debug_summary, "roundtrip_status": roundtrip_status}, ensure_ascii=False, sort_keys=True, default=str),
    )
    if not (readback_ok and compare_ok):
        logging.warning(
            "phase_c_weather_readback_mismatch target_date(JST)=%s run_id=%s save_reason=%s merge_status=%s missing_fields=%s mismatch_fields=%s",
            summary.target_date,
            run_id,
            save_result.get("reason"),
            stage_status,
            readback_failures,
            compare_mismatch_fields,
        )
    return refreshed_summary or summary


def _compute_expense_f_alert(
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> dict[str, Any]:
    logging.info(
        "expense_f_start target_date(JST)=%s run_id=%s",
        summary.target_date,
        run_id,
    )
    aggregate = aggregate_daily_expense_f(summary.target_date)
    matched = aggregate.available and aggregate.count > 0
    reasons: list[str] = []
    if matched:
        reasons.append(f"件数: {aggregate.count} 件")
        if aggregate.total > 0:
            reasons.append(f"合計金額: {aggregate.total:.0f} 円")
        if aggregate.merchants:
            reasons.append(f"代表merchant: {', '.join(aggregate.merchants[:3])}")
        if aggregate.first_time or aggregate.last_time:
            time_label = f"{aggregate.first_time or '不明'} 〜 {aggregate.last_time or '不明'}"
            reasons.append(f"発生時刻帯: {time_label}")
        reasons.append("再発防止: 同時刻帯と同merchantの支出前に必要性を10秒確認する")

    reason_labels = [reason.split(":")[0] if ":" in reason else reason for reason in reasons[:3]]
    logging.info(
        "[ExpenseF] source=expenses_db_direct target_date=%s matched=%s count=%s total=%s merchants_count=%s data_status=%s skip_reason=%s resolved_props=%s created_time_source=%s date_window_start=%s date_window_end=%s filter_strategy=%s query_exception_class=%s query_exception_message=%s matched_count=%s total_amount=%s reason_labels=%s",
        summary.target_date,
        matched,
        aggregate.count,
        aggregate.total,
        len(aggregate.merchants),
        aggregate.data_status,
        aggregate.skip_reason,
        aggregate.debug_summary.get("resolved_props"),
        aggregate.debug_summary.get("created_time_source"),
        aggregate.debug_summary.get("date_window_start"),
        aggregate.debug_summary.get("date_window_end"),
        aggregate.debug_summary.get("filter_strategy"),
        aggregate.debug_summary.get("query_exception_class"),
        aggregate.debug_summary.get("query_exception_message"),
        aggregate.debug_summary.get("matched_count"),
        aggregate.debug_summary.get("total_amount"),
        reason_labels,
    )
    if matched:
        logging.info(
            "expense_f_alert_matched target_date(JST)=%s run_id=%s matched=%s reason_labels=%s",
            summary.target_date,
            run_id,
            matched,
            reason_labels,
        )
    else:
        logging.info(
            "expense_f_alert_not_matched target_date(JST)=%s run_id=%s matched=%s",
            summary.target_date,
            run_id,
            matched,
        )

    return {
        "matched": matched,
        "title": "望ましくない支出（Fプロパティ）",
        "summary": (
            f"{summary.target_date} に Fプロパティ付きの望ましくない支出を検知しました。再発防止の判断に使ってください。"
            if matched
            else ""
        ),
        "reasons": reasons,
        "debug": {
            "data_status": aggregate.data_status,
            "skip_reason": aggregate.skip_reason,
            "debug_summary": aggregate.debug_summary,
        },
    }


def _generate_and_save_expense_f(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> dict[str, Any]:
    del config
    return _compute_expense_f_alert(summary=summary, run_id=run_id)


def _compute_f_risk_alert_runtime(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> dict[str, Any]:
    logging.info("f_risk_runtime_start source=f_risk_runtime target_date(JST)=%s run_id=%s", summary.target_date, run_id)
    expense_f_aggregate = aggregate_daily_expense_f(summary.target_date)
    store = FRiskStateStore()
    if store.meta.backend == "unavailable":
        logging.warning(
            "[FRisk] source=f_risk_runtime target_date=%s state_store_backend=%s state_read_ok=%s state_write_ok=%s branch_name=%s path=%s fallback_used=%s skip_reason=state_backend_unavailable",
            summary.target_date,
            store.meta.backend,
            store.meta.state_read_ok,
            store.meta.state_write_ok,
            store.meta.branch_name,
            store.meta.path,
            store.meta.fallback_used,
        )
    previous_state = store.get_for_date(summary.target_date)
    hash_payload = {
        "target_date": summary.target_date,
        "sleep": {
            "sleep_hours": summary.resolved_sleep_duration_hours,
            "sleep_score": summary.sleep_score,
        },
        "weather": {
            "weather_code": summary.weather_code,
            "weather_temp_max_c": summary.weather_temp_max_c,
            "weather_temp_min_c": summary.weather_temp_min_c,
            "weather_precip_probability_max": summary.weather_precip_probability_max,
        },
        "expense_f": {
            "count": expense_f_aggregate.count,
            "total": expense_f_aggregate.total,
            "data_status": expense_f_aggregate.data_status,
        },
    }
    current_input_hash, normalized_hash_payload, _ = _build_input_hash(hash_payload)
    previous_input_hash = (previous_state.get("input_hash") or "").strip() or None
    input_changed = current_input_hash != previous_input_hash
    logging.info(
        "f_risk_runtime_input_summary source=f_risk_runtime target_date(JST)=%s run_id=%s current_input_hash=%s previous_input_hash=%s input_hash_changed=%s state_store_backend=%s state_read_ok=%s branch_name=%s path=%s fallback_used=%s debug_summary=%s",
        summary.target_date,
        run_id,
        current_input_hash,
        previous_input_hash,
        input_changed,
        store.meta.backend,
        store.meta.state_read_ok,
        store.meta.branch_name,
        store.meta.path,
        store.meta.fallback_used,
        json.dumps(
            {
                "hash_input_summary": normalized_hash_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    )

    try:
        result = generate_f_risk(
            daily_log_read_url=config.daily_log_read_url,
            bearer_token=config.bearer_token,
            target_date=summary.target_date,
        )
    except Exception as exc:  # noqa: BLE001
        logging.exception(
            "phase_c_f_risk_failed target_date(JST)=%s run_id=%s reason=%s",
            summary.target_date,
            run_id,
            str(exc),
        )
        raise
    if result.skip_reason:
        logging.info(
            "[FRisk] source=f_risk_runtime target_date=%s skip_reason=%s state_store_backend=%s risk_matched=false score=%s no_alert_reason=%s matched_patterns=%s daily_log_write_skipped_for_f_risk=true",
            summary.target_date,
            result.skip_reason,
            store.meta.backend,
            result.score,
            (result.debug_summary.get("risk_json") or {}).get("no_alert_reason"),
            result.matched_patterns[:3],
        )
    row = {
        "input_hash": current_input_hash,
        "reason": result.reason or (result.skip_reason or ""),
        "generated_at": _utc_timestamp(),
        "score": result.score,
        "matched_patterns": result.matched_patterns,
        "alert_text": result.alert_text,
        "no_alert_reason": (result.debug_summary.get("risk_json") or {}).get("no_alert_reason"),
    }
    state_write_ok = store.save_for_date(summary.target_date, row)
    logging.info(
        "f_risk_runtime_result source=f_risk_runtime target_date=%s current_input_hash=%s previous_input_hash=%s input_hash_changed=%s state_store_backend=%s state_read_ok=%s state_write_ok=%s branch_name=%s path=%s fallback_used=%s risk_matched=%s score=%s skip_reason=%s no_alert_reason=%s matched_patterns=%s daily_log_write_skipped_for_f_risk=true",
        summary.target_date,
        current_input_hash,
        previous_input_hash,
        input_changed,
        store.meta.backend,
        store.meta.state_read_ok,
        state_write_ok,
        store.meta.branch_name,
        store.meta.path,
        store.meta.fallback_used,
        bool(result.alert_text),
        result.score,
        result.skip_reason,
        row.get("no_alert_reason"),
        result.matched_patterns[:3],
    )
    return {
        "matched": bool(result.alert_text),
        "alert_text": result.alert_text or "",
        "score": result.score,
        "reason": row["reason"],
        "matched_patterns": result.matched_patterns,
        "skip_reason": result.skip_reason,
        "no_alert_reason": row.get("no_alert_reason"),
        "state_meta": {
            "backend": store.meta.backend,
            "state_read_ok": store.meta.state_read_ok,
            "state_write_ok": state_write_ok,
            "branch_name": store.meta.branch_name,
            "path": store.meta.path,
            "fallback_used": store.meta.fallback_used,
        },
    }


def _generate_and_save_f_risk(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> "DailyLogSummary":
    _compute_f_risk_alert_runtime(config, summary=summary, run_id=run_id)
    return _refresh_daily_log_summary(config, summary.target_date) or summary


def _generate_and_save_diary(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
    reloaded_after_sleep_save: bool = False,
) -> "DailyLogSummary":
    logging.info("phase_c_diary_start target_date(JST)=%s run_id=%s", summary.target_date, run_id)
    diary_input_fields, skipped_fields, input_overview, skipped_reason_by_field = build_diary_input_fields(summary)
    _assert_diary_input_consistency(diary_input_fields)
    diary_hash_payload, diary_hash_summary = _build_diary_hash_payload(summary, diary_input_fields)
    current_input_hash, normalized_hash_payload, _ = _build_input_hash(diary_hash_payload)
    previous_input_hash = (summary.diary_input_hash or "").strip() or None
    has_diary = bool((summary.diary or "").strip())
    input_changed = current_input_hash != previous_input_hash
    logging.info(
        "phase_c_diary_input_summary target_date(JST)=%s run_id=%s used_fields=%s skipped_fields=%s skipped_reason_by_field=%s input_overview=%s debug_summary=%s",
        summary.target_date,
        run_id,
        sorted(diary_input_fields.keys()),
        skipped_fields,
        json.dumps(skipped_reason_by_field, ensure_ascii=False, sort_keys=True),
        input_overview,
        json.dumps(
            {
                **diary_hash_summary,
                "reloaded_after_sleep_save": reloaded_after_sleep_save,
                "current_input_hash": current_input_hash,
                "previous_input_hash": previous_input_hash,
                "input_hash_changed": input_changed,
                "has_previous_input_hash": previous_input_hash is not None,
                "has_diary": has_diary,
                "hash_input_summary": normalized_hash_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    )
    if not diary_input_fields:
        logging.info(
            "phase_c_diary_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=no_daily_log generated_properties=[]",
            summary.target_date,
            run_id,
            False,
        )
        refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
        return refreshed_summary or summary

    if has_diary and not input_changed:
        logging.info(
            "phase_c_diary_skip target_date(JST)=%s run_id=%s skip_reason=unchanged_input current_input_hash=%s previous_input_hash=%s input_hash_changed=%s input_summary=%s",
            summary.target_date,
            run_id,
            current_input_hash,
            previous_input_hash,
            input_changed,
            json.dumps(diary_hash_summary, ensure_ascii=False, sort_keys=True),
        )
        logging.info(
            "phase_c_diary_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=unchanged_input generated_properties=[]",
            summary.target_date,
            run_id,
            False,
        )
        refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
        return refreshed_summary or summary

    generated_diary = generate_diary_from_daily_log(diary_input_fields, summary.target_date)
    logging.info(
        "phase_c_diary_generated target_date(JST)=%s run_id=%s generated_properties=%s chars=%s",
        summary.target_date,
        run_id,
        ["diary"],
        len(generated_diary.strip()),
    )
    save_result = _save_daily_log_fields(
        config,
        target_date=summary.target_date,
        payload={
            "diary": generated_diary,
            "diary_input_hash": current_input_hash,
            "diary_generated_at": _utc_timestamp(),
        },
    )
    logging.info(
        "phase_c_diary_saved target_date(JST)=%s run_id=%s updated=%s reason=%s generated_properties=%s current_input_hash=%s previous_input_hash=%s input_hash_changed=%s",
        summary.target_date,
        run_id,
        save_result.get("updated"),
        save_result.get("reason"),
        ["diary"],
        current_input_hash,
        previous_input_hash,
        input_changed,
    )
    refreshed_summary = _refresh_daily_log_summary(config, summary.target_date)
    return refreshed_summary or summary


def _notify_phase_c(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> bool:
    logging.info("phase_c_notify_start target_date(JST)=%s run_id=%s", summary.target_date, run_id)
    if summary.diary_notification_sent is True:
        logging.info(
            "phase_c_notify_skipped_already_sent target_date(JST)=%s run_id=%s skip_reason=already_notified",
            summary.target_date,
            run_id,
        )
        return False
    page_url = (summary.page_url or "").strip()
    if not page_url:
        logging.info(
            "phase_c_notify_skipped target_date(JST)=%s run_id=%s skip_reason=missing_page_url",
            summary.target_date,
            run_id,
        )
        return False
    logging.info(
        "phase_c_notify_skipped target_date(JST)=%s run_id=%s skip_reason=email_disabled",
        summary.target_date,
        run_id,
    )
    return False


def run_notify_diary(config: Config, target_date: str, run_id: str) -> None:
    logging.info("phase_c_start target_date(JST)=%s run_id=%s", target_date, run_id)
    summary = _refresh_daily_log_summary(config, target_date)
    if not summary:
        logging.info("phase_c_sleep_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=no_daily_log generated_properties=[]", target_date, run_id, False)
        return
    def _run_optional_enrichment(step_name: str, fn, current_summary: "DailyLogSummary") -> "DailyLogSummary":
        try:
            return fn(config, summary=current_summary, run_id=run_id)
        except Exception as exc:  # noqa: BLE001
            logging.exception(
                "phase_c_optional_step_failed target_date(JST)=%s run_id=%s step=%s exception_class=%s exception_message=%s failing_stage=%s",
                current_summary.target_date,
                run_id,
                step_name,
                exc.__class__.__name__,
                str(exc),
                step_name,
            )
            return current_summary

    summary = _run_optional_enrichment("weather", _generate_and_save_weather, summary)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary
    try:
        expense_f_alert = _compute_expense_f_alert(summary=summary, run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        logging.exception(
            "phase_c_optional_step_failed target_date(JST)=%s run_id=%s step=expense_f exception_class=%s exception_message=%s failing_stage=expense_f",
            summary.target_date,
            run_id,
            exc.__class__.__name__,
            str(exc),
        )
        expense_f_alert = {"matched": False, "title": "望ましくない支出（Fプロパティ）", "summary": "", "reasons": [], "debug": {"error": str(exc)}}
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary
    summary = _run_optional_enrichment("sleep", _generate_and_save_sleep_insights, summary)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary
    summary = _run_optional_enrichment("notes_label", _ensure_notes_label_persisted, summary)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary
    summary = _run_optional_enrichment("f_risk", _generate_and_save_f_risk, summary)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary
    summary = _generate_and_save_today_advice(config, summary=summary, run_id=run_id)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary
    summary = _generate_and_save_diary(config, summary=summary, run_id=run_id, reloaded_after_sleep_save=True)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary

    if not (summary.diary or "").strip():
        logging.info(
            "phase_c_notify_skipped target_date(JST)=%s run_id=%s skip_reason=no_daily_log",
            summary.target_date,
            run_id,
        )
        return
    if expense_f_alert.get("matched"):
        logging.info(
            "phase_c_notify_expense_f_alert target_date(JST)=%s run_id=%s matched=%s reasons=%s",
            summary.target_date,
            run_id,
            expense_f_alert.get("matched"),
            (expense_f_alert.get("reasons") or [])[:3],
        )
    sent = _notify_phase_c(config, summary=summary, run_id=run_id)
    if sent:
        post_json(
            config.diary_mark_notified_url,
            {"target_date": summary.target_date},
            config.bearer_token,
        )
        logging.info(
            "phase_c_notify_sent target_date(JST)=%s run_id=%s notified_updated=%s",
            summary.target_date,
            run_id,
            True,
        )
    logging.info("phase_c_end target_date(JST)=%s run_id=%s", summary.target_date, run_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily diary automation.")
    parser.add_argument(
        "--phase",
        choices=("ingest", "publish", "notify_diary", "all"),
        default="all",
        help="Phase to run (default: all).",
    )
    parser.add_argument(
        "--target-date",
        help="Target date in JST (YYYY-MM-DD). Default is yesterday; for notify_diary/all you can override via TODAY_ADVICE_TARGET_MODE=TODAY.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    need_ingest = args.phase in ("ingest", "all")
    need_publish = args.phase in ("publish", "all")
    need_notify_diary = args.phase in ("notify_diary", "all")
    config = load_config(
        need_mail=need_publish,
        need_tasks=need_ingest,
    )
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    target_date = resolve_target_date(explicit_target_date=args.target_date, phase=args.phase)

    logging.info(
        "Starting daily job. phase=%s target_date(JST)=%s run_id=%s",
        args.phase,
        target_date,
        run_id,
    )
    logging.info(
        "runtime_identity workflow_name=%s phase=%s github_sha=%s branch_name=%s",
        os.getenv("GITHUB_WORKFLOW", ""),
        args.phase,
        os.getenv("GITHUB_SHA", ""),
        os.getenv("GITHUB_REF_NAME", ""),
    )

    if args.phase in ("ingest", "all"):
        run_ingest(config, target_date, run_id)
    if args.phase in ("publish", "all"):
        run_publish(config, target_date, run_id)
    if args.phase in ("notify_diary", "all"):
        run_notify_diary(config, target_date, run_id)


if __name__ == "__main__":
    main()
