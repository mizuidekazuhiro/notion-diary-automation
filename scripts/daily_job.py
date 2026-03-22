from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
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
from scripts.mood_advice_generator import (
    build_today_advice_generation_context,
    generate_today_advice,
)
from scripts.sleep_condition_generator import (
    load_recent_daily_logs,
    maybe_generate_sleep_insights,
)

JST = ZoneInfo("Asia/Tokyo")


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

    mail = render_mail(summary)
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


def build_diary_input_fields(summary: "DailyLogSummary") -> tuple[dict[str, str], list[str], str]:
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
        ("Sleep Duration", str(summary.sleep_duration_min) if summary.sleep_duration_min is not None else None),
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
        ("Sleep Analysis JP", summary.sleep_analysis_jp),
        ("Today Condition Forecast JP", summary.today_condition_forecast_jp),
        ("Today advice", summary.today_advice),
    ]

    used: dict[str, str] = {}
    skipped: list[str] = []
    overview_parts: list[str] = []

    for name, raw in candidates:
        value = (raw or "").strip()
        if not value:
            skipped.append(name)
            continue
        used[name] = value
        preview = value.replace("\n", " ")
        if len(preview) > 80:
            preview = f"{preview[:80]}..."
        overview_parts.append(f"{name}({len(value)} chars): {preview}")

    return used, skipped, " | ".join(overview_parts)


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
    }
    return hash_payload, debug_summary


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
    sleep_payload = maybe_generate_sleep_insights(
        target_date=summary.target_date,
        today_summary=summary,
        history_summaries=history_summaries,
    )
    if not sleep_payload:
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


def _generate_and_save_diary(
    config: Config,
    *,
    summary: "DailyLogSummary",
    run_id: str,
) -> "DailyLogSummary":
    logging.info("phase_c_diary_start target_date(JST)=%s run_id=%s", summary.target_date, run_id)
    diary_input_fields, skipped_fields, input_overview = build_diary_input_fields(summary)
    diary_hash_payload, diary_hash_summary = _build_diary_hash_payload(summary, diary_input_fields)
    current_input_hash, normalized_hash_payload, _ = _build_input_hash(diary_hash_payload)
    previous_input_hash = (summary.diary_input_hash or "").strip() or None
    has_diary = bool((summary.diary or "").strip())
    input_changed = current_input_hash != previous_input_hash
    logging.info(
        "phase_c_diary_input_summary target_date(JST)=%s run_id=%s used_fields=%s skipped_fields=%s input_overview=%s debug_summary=%s",
        summary.target_date,
        run_id,
        sorted(diary_input_fields.keys()),
        skipped_fields,
        input_overview,
        json.dumps(
            {
                **diary_hash_summary,
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
    summary = _refresh_daily_log_summary(config, target_date)
    if not summary:
        logging.info("phase_c_sleep_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=no_daily_log generated_properties=[]", target_date, run_id, False)
        return
    summary = _generate_and_save_sleep_insights(config, summary=summary, run_id=run_id)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary
    summary = _generate_and_save_today_advice(config, summary=summary, run_id=run_id)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary
    summary = _generate_and_save_diary(config, summary=summary, run_id=run_id)
    summary = _refresh_daily_log_summary(config, summary.target_date) or summary

    if not (summary.diary or "").strip():
        logging.info(
            "phase_c_notify_skipped target_date(JST)=%s run_id=%s skip_reason=no_daily_log",
            summary.target_date,
            run_id,
        )
        return
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

    if args.phase in ("ingest", "all"):
        run_ingest(config, target_date, run_id)
    if args.phase in ("publish", "all"):
        run_publish(config, target_date, run_id)
    if args.phase in ("notify_diary", "all"):
        run_notify_diary(config, target_date, run_id)


if __name__ == "__main__":
    main()
