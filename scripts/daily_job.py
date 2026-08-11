from __future__ import annotations

import argparse
import hashlib
import dataclasses
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
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
from publish.render_diary_notification_mail import render_diary_notification_mail
from publish.render_mail import render_mail
from publish.send_mail import MailConfig, send_mail
from scripts.mail_dedupe import (
    MAIL_INPUT_HASH_FIELDS,
    build_mail_input_snapshot,
    decide_mail_send,
    execute_with_update_on_success,
    sha256_hex,
    snapshot_json,
)
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
from scripts.daily_job_phase_c import PhaseCDeps, PhaseSemanticDegradation, run_phase_c
from scripts.voice_diary_notes import (
    fetch_voice_diary_notes,
    format_voice_diary_notes,
    mark_voice_diary_notes_used,
)
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
MAIL_INPUT_SNAPSHOT_MAX_CHARS = 1900


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
    study_reconcile_url: str = ""
    mail_cc: List[str] = field(default_factory=list)
    mail_bcc: List[str] = field(default_factory=list)


WORKER_EXECUTE_BASE_PATH = "/execute/api/daily_log"
WORKER_ENDPOINTS = {
    "ensure": f"{WORKER_EXECUTE_BASE_PATH}/ensure",
    "ingest_health": f"{WORKER_EXECUTE_BASE_PATH}/ingest_health",
    "ingest_expenses": f"{WORKER_EXECUTE_BASE_PATH}/ingest_expenses",
    "generate_diary": f"{WORKER_EXECUTE_BASE_PATH}/generate_diary",
    "mark_diary_notified": f"{WORKER_EXECUTE_BASE_PATH}/mark_diary_notified",
    "read": "/api/daily_log",
    "study_reconcile": "/execute/api/study/reconcile",
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
    mail_cc = [item.strip() for item in os.getenv("MAIL_CC", "").split(",") if item.strip()]
    mail_bcc = [item.strip() for item in os.getenv("MAIL_BCC", "").split(",") if item.strip()]

    daily_log_upsert_url = read_env("DAILY_LOG_UPSERT_URL", True)

    return Config(
        mail_from=read_env("MAIL_FROM", need_mail),
        mail_to=mail_to,
        mail_cc=mail_cc,
        mail_bcc=mail_bcc,
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
        study_reconcile_url=build_worker_url(
            daily_log_upsert_url, WORKER_ENDPOINTS["study_reconcile"]
        ),
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


def _get_mail_metadata_persist_verify_retries() -> int:
    raw = os.getenv("MAIL_METADATA_PERSIST_VERIFY_RETRIES", "3")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 3
    return value if value > 0 else 3


def _get_mail_metadata_persist_verify_backoff_seconds() -> float:
    raw = os.getenv("MAIL_METADATA_PERSIST_VERIFY_BACKOFF_SECONDS", "2")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 2.0
    return value if value >= 0 else 2.0




def _classify_meal_photo_url(url: str) -> str:
    lowered = (url or "").strip().lower()
    if lowered.startswith("https://"):
        return "dropbox" if "dropbox.com" in lowered else "https"
    if lowered.startswith("file://"):
        return "notion_file"
    if "notion" in lowered:
        return "notion_file"
    return "invalid"


def _is_renderable_photo_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    if not lowered.startswith("https://"):
        return False
    if "dropbox.com" in lowered:
        return "raw=1" in lowered
    return any(lowered.split("?", 1)[0].endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))

def run_ingest(config: Config, target_date: str, run_id: str) -> None:
    debug_readback = os.getenv("DAILY_LOG_DEBUG_READBACK_AFTER_EACH_STEP", "").lower() == "true"

    def _readback(step_name: str) -> None:
        if not debug_readback:
            return
        summary = read_daily_log(
            daily_log_read_url=config.daily_log_read_url,
            target_date=target_date,
            bearer_token=config.bearer_token,
        )
        meal_count = len(getattr(summary, "meal_photos", []) or []) if summary else -1
        page_id = getattr(summary, "page_id", "") if summary else ""
        logging.info("phase1_debug_readback step=%s page_id=%s meal_photos_count=%s", step_name, page_id, meal_count)

    title = f"Daily Logï½œ{target_date}"
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
    _readback("ensure")

    if config.study_reconcile_url:
        try:
            reconcile_result = post_json(
                config.study_reconcile_url,
                {"target_date": target_date},
                config.bearer_token,
            )
            logging.info(
                "study_reconcile target_date(JST)=%s updated=%s authoritative_anki=%s",
                target_date,
                reconcile_result.get("daily_log_updated"),
                (reconcile_result.get("daily_totals") or {}).get("anki_revlog_authoritative"),
            )
        except Exception as exc:
            logging.warning(
                "study_reconcile_failed target_date(JST)=%s exception_class=%s exception_message=%s",
                target_date,
                exc.__class__.__name__,
                str(exc),
            )
    _readback("study_reconcile")

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
        after_step=_readback,
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
    target_date_matches = summary.target_date == target_date
    summary_page_id = getattr(summary, "page_id", "") or ""
    summary_location_summary = (getattr(summary, "location_summary", "") or "").strip()
    summary_meal_photos = getattr(summary, "meal_photos", []) or []
    summary_mail_id = (getattr(summary, "mail_id", "") or "").strip()
    summary_diary_notification_sent = getattr(summary, "diary_notification_sent", None)
    summary_today_advice = (getattr(summary, "today_advice", "") or "").strip()
    summary_diary = (getattr(summary, "diary", "") or "").strip()
    summary_mail_input_hash = (getattr(summary, "mail_input_hash", "") or "").strip()
    duplicate_info = getattr(summary, "duplicate_info", {}) or {}
    duplicate_fields_present = duplicate_info.get("duplicate_fields_present", {}) if isinstance(duplicate_info, dict) else {}
    logging.info(
        "phase04_daily_log_diagnostics target_date=%s canonical_page_id=%s daily_log_duplicate_detected=%s duplicate_count=%s duplicate_page_ids_count=%s duplicate_merge_completed=%s duplicate_merged_fields=%s duplicate_has_location_summary=%s duplicate_has_meal_photos=%s location_summary_present=%s location_summary_source=%s location_summary_chars=%s meal_photos_count=%s meal_photo_source_extraction_failed_count=%s today_advice_present=%s diary_present=%s weather_present=%s mail_id_present=%s diary_notification_sent=%s mail_input_hash_present=%s mail_input_snapshot_present=%s",
        target_date,
        duplicate_info.get("canonical_page_id") or summary_page_id,
        bool(duplicate_info.get("detected")),
        int(duplicate_info.get("duplicate_count") or 0),
        len(duplicate_info.get("duplicate_page_ids") or []),
        bool(duplicate_info.get("merge_completed")),
        ",".join(duplicate_info.get("merged_fields") or []),
        bool(duplicate_fields_present.get("location_summary")),
        bool(duplicate_fields_present.get("meal_photos")),
        bool(summary_location_summary),
        str(getattr(summary, "location_summary_source", "empty") or "empty"),
        len(summary_location_summary),
        len(summary_meal_photos),
        getattr(summary, "meal_photo_source_extraction_failed_count", 0),
        bool(summary_today_advice),
        bool(summary_diary),
        bool((getattr(summary, "weather_summary", "") or "").strip() or getattr(summary, "weather_code", None) is not None),
        bool(summary_mail_id),
        summary_diary_notification_sent,
        bool(summary_mail_input_hash),
        False,
    )
    if not target_date_matches:
        raise RuntimeError(f"Phase04 fail: target_date mismatch expected={target_date} actual={summary.target_date}")
    if not summary_today_advice:
        raise RuntimeError("Phase04 fail: Today advice is empty.")
    if not summary_diary:
        logging.warning("Phase04 warning: Diary is empty.")
    if not summary_mail_id:
        logging.warning("Phase04 warning: Mail ID is empty.")
    if summary_diary_notification_sent is False:
        logging.warning("Phase04 warning: Diary Notification Sent is false before publish.")

    meal_photo_url_types = sorted({_classify_meal_photo_url(url) for url in summary_meal_photos})
    location_source = str(getattr(summary, "location_summary_source", "empty") or "empty").strip() or "empty"
    meal_photo_renderable_count = sum(1 for url in summary_meal_photos if _is_renderable_photo_url(url))
    logging.info(
        "publish_inputs location_summary_present=%s location_summary_source=%s meal_photos_count=%s meal_photo_url_types=%s meal_photo_renderable_count=%s meal_photo_source_extraction_failed_count=%s",
        bool(summary_location_summary),
        location_source,
        len(summary_meal_photos),
        ",".join(meal_photo_uÛyæÚ$z{-®éÜj×÷&RÀ¢6ÖU÷66÷&U÷7G&V²À¢¢7FFU÷w&—FUöö²Ò7F÷&Rç6fUöf÷%öFFR†e÷&—6µ÷F&vWEöFFRÂ&÷rĞ¢Æövv–æræ–æfò€Ğ¢&e÷&—6µ÷'VçF–ÖU÷&W7VÇB6÷W&6SÖe÷&—6µ÷'VçF–ÖRF&vWEöFFSÒW27W'&VçEö–çWEö†6ƒÒW2&Wf–÷W5ö–çWEö†6ƒÒW2–çWEö†6…ö6†ævVCÒW27FFU÷7F÷&Uö&6¶VæCÒW27FFU÷&VEöö³ÒW27FFU÷w&—FUöö³ÒW2'&æ6…öæÖSÒW2FƒÒW2fÆÆ&6µ÷W6VCÒW2&—6µöÖF6†VCÒW266÷&SÒW26¶—÷&V6öãÒW2æõöÆW'E÷&V6öãÒW2ÖF6†VE÷GFW&ç3ÒW2F–Ç•öÆöu÷w&—FU÷6¶—VEöf÷%öe÷&—6³×G'VR"ÀĞ¢e÷&—6µ÷F&vWEöFFRÀĞ¢7W'&VçEö–çWEö†6‚ÀĞ¢&Wf–÷W5ö–çWEö†6‚ÀĞ¢–çWEö6†ævVBÀĞ¢7F÷&RæÖWFæ&6¶VæBÀĞ¢7F÷&RæÖWFç7FFU÷&VEöö²ÀĞ¢7FFU÷w&—FUöö²ÀĞ¢7F÷&RæÖWFæ'&æ6…öæÖRÀĞ¢7F÷&RæÖWFçF‚ÀĞ¢7F÷&RæÖWFæfÆÆ&6µ÷W6VBÀĞ¢&ööÂ‡&—6µö§6öâævWB‚'&—6µöÖF6†VB"’’À¢&W7VÇBç66÷&RÀĞ¢&W7VÇBç6¶—÷&V6öâÀĞ¢&÷rævWB‚&æõöÆW'E÷&V6öâ"’ÀĞ¢&W7VÇBæÖF6†VE÷GFW&ç5³£5ÒÀĞ¢Ğ¢&WGW&â°Ğ¢&ÖF6†VB#¢&ööÂ‡&—6µö§6öâævWB‚'&—6µöÖF6†VB"Â&ööÂ‡&W7VÇBæÆW'E÷FW‡B’’’À¢&ÆW'E÷FW‡B#¢&W7VÇBæÆW'E÷FW‡B÷"""ÀĞ¢'66÷&R#¢&W7VÇBç66÷&RÀĞ¢'&V6öâ#¢&÷u²'&V6öâ%ÒÀĞ¢&ÖF6†VE÷GFW&ç2#¢&W7VÇBæÖF6†VE÷GFW&ç2ÀĞ¢'6¶—÷&V6öâ#¢&W7VÇBç6¶—÷&V6öâÀĞ¢&æõöÆW'E÷&V6öâ#¢&÷rævWB‚&æõöÆW'E÷&V6öâ"’ÀĞ¢&–çWEö†6‚#¢7W'&VçEö–çWEö†6‚À¢&vVæW&FVEöB#¢vVæW&FVEöBÀ¢'&—6µöÆWfVÂ#¢&÷rævWB‚'&—6µöÆWfVÂ"’À¢&FF÷7FGW2#¢FF÷7FGW2À¢&fÆÆ&6µ÷W6VB#¢&÷rævWB‚&fÆÆ&6µ÷W6VB"’À¢&ÖÅ÷6¶—VE÷&V6öâ#¢&÷rævWB‚&ÖÅ÷6¶—VE÷&V6öâ"’À¢&eöWfVçEö6÷VçB#¢&÷rævWB‚&eöWfVçEö6÷VçB"’À¢'W6&ÆUöeöWfVçEö6÷VçB#¢&÷rævWB‚'W6&ÆUöeöWfVçEö6÷VçB"’À¢'6–Ö–Æ&—G•÷66÷&R#¢&÷rævWB‚'6–Ö–Æ&—G•÷66÷&R"’À¢&†—7F÷'•ö6÷VçB#¢&÷rævWB‚&†—7F÷'•ö6÷VçB"’À¢'6ÖU÷66÷&U÷7G&V²#¢6ÖU÷66÷&U÷7G&V²À¢'VÆ—G•÷v&æ–æw2#¢VÆ—G•÷v&æ–æw2À¢'7FFUöÖWF#¢°Ğ¢&&6¶VæB#¢7F÷&RæÖWFæ&6¶VæBÀĞ¢'7FFU÷&VEöö²#¢7F÷&RæÖWFç7FFU÷&VEöö²ÀĞ¢'7FFU÷w&—FUöö²#¢7FFU÷w&—FUöö²ÀĞ¢&'&æ6…öæÖR#¢7F÷&RæÖWFæ'&æ6…öæÖRÀĞ¢'F‚#¢7F÷&RæÖWFçF‚ÀĞ¢&fÆÆ&6µ÷W6VB#¢7F÷&RæÖWFæfÆÆ&6µ÷W6VBÀĞ¢'&WW6VE÷&Wf–÷W5÷7FFR#¢fÇ6RÀĞ¢ÒÀĞ¢ĞĞ Ğ Ğ¦FVbövVæW&FUöæE÷6fUöe÷&—6²€¢6öæf–s¢6öæf–rÀĞ¢¢ÀĞ¢7VÖÖ'“¢$F–Ç”Æöu7VÖÖ'’"ÀĞ¢'Våö–C¢7G"ÀĞ¢’Óâ$F–Ç”Æöu7VÖÖ'’# ¢'VçF–ÖRÒö6ö×WFUöe÷&—6µöÆW'E÷'VçF–ÖR†6öæf–rÂ7VÖÖ'“×7VÖÖ'’Â'Våö–C×'Våö–B¢–b'VçF–ÖRævWB‚&FF÷7FGW2"’–â²&f–ÆVB"Â&FVw&FVB'Ò÷"'VçF–ÖRævWB‚&fÆÆ&6µ÷W6VB"’÷"'VçF–ÖRævWB‚'6¶—÷&V6öâ"“ ¢&—6R†6U6VÖçF–4FVw&FF–öâ€¢7G"‡'VçF–ÖRævWB‚'6¶—÷&V6öâ"’÷"'VçF–ÖRævWB‚&ÖÅ÷6¶—VE÷&V6öâ"’÷"'VçF–ÖRævWB‚&FF÷7FGW2"’¢¢&WGW&â÷&Vg&W6…öF–Ç•öÆöu÷7VÖÖ'’†6öæf–rÂ7VÖÖ'’çF&vWEöFFR’÷"7VÖÖ' Ğ Ğ¦FVbövVæW&FUöæE÷6fUöF–'’€Ğ¢6öæf–s¢6öæf–rÀĞ¢¢ÀĞ¢7VÖÖ'“¢$F–Ç”Æöu7VÖÖ'’"ÀĞ¢'Våö–C¢7G"ÀĞ¢&VÆöFVEögFW%÷6ÆVW÷6fS¢&ööÂÒfÇ6RÀĞ¢’Óâ$F–Ç”Æöu7VÖÖ'’# Ğ¢Æövv–æræ–æfò‚'†6Uö5öF–'•÷7F'BF&vWEöFFR„¥5B“ÒW2'Våö–CÒW2"Â7VÖÖ'’çF&vWEöFFRÂ'Våö–BĞ¢fö–6Uöæ÷FW2ÒfWF6…÷fö–6UöF–'•öæ÷FW2‡7VÖÖ'’çF&vWEöFFRĞ¢fö–6Uöæ÷FW5÷FW‡BÒf÷&ÖE÷fö–6UöF–'•öæ÷FW2‡fö–6Uöæ÷FW2Ğ¢–bfö–6Uöæ÷FW5÷FW‡C Ğ¢Æövv–æræ–æfò‚'fö–6UöF–'•öæ÷FW5öFFVE÷FõöF–'•ö–çWG2F&vWEöFFSÒW26÷VçCÒW26†'3ÒW2"Â7VÖÖ'’çF&vWEöFFRÂÆVâ‡fö–6Uöæ÷FW2’ÂÆVâ‡fö–6Uöæ÷FW5÷FW‡B’Ğ¢F–'•ö–çWEöf–VÆG2Â6¶—VEöf–VÆG2Â–çWEö÷fW'f–WrÂ6¶—VE÷&V6öåö'•öf–VÆBÒ'V–ÆEöF–'•ö–çWEöf–VÆG2‡7VÖÖ'’Âfö–6UöF–'•öæ÷FW5÷FW‡C×fö–6Uöæ÷FW5÷FW‡BĞ¢ö76W'EöF–'•ö–çWEö6öç6—7FVæ7’†F–'•ö–çWEöf–VÆG2Ğ¢F–'•ö†6…÷–ÆöBÂF–'•ö†6…÷7VÖÖ'’Òö'V–ÆEöF–'•ö†6…÷–ÆöB‡7VÖÖ'’ÂF–'•ö–çWEöf–VÆG2Ğ¢7W'&VçEö–çWEö†6‚Âæ÷&ÖÆ—¦VEö†6…÷–ÆöBÂòÒö'V–ÆEö–çWEö†6‚†F–'•ö†6…÷–ÆöBĞ¢&Wf–÷W5ö–çWEö†6‚Ò‡7VÖÖ'’æF–'•ö–çWEö†6‚÷"""’ç7G&—‚’÷"æöæPĞ¢†5öF–'’Ò&ööÂ‚‡7VÖÖ'’æF–'’÷"""’ç7G&—‚’Ğ¢–çWEö6†ævVBÒ7W'&VçEö–çWEö†6‚Ò&Wf–÷W5ö–çWEö†6€Ğ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öF–'•ö–çWE÷7VÖÖ'’F&vWEöFFR„¥5B“ÒW2'Våö–CÒW2W6VEöf–VÆG3ÒW26¶—VEöf–VÆG3ÒW26¶—VE÷&V6öåö'•öf–VÆCÒW2–çWEö÷fW'f–WsÒW2FV'Vu÷7VÖÖ'“ÒW2"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢6÷'FVB†F–'•ö–çWEöf–VÆG2æ¶W—2‚’’ÀĞ¢6¶—VEöf–VÆG2ÀĞ¢§6öâæGV×2‡6¶—VE÷&V6öåö'•öf–VÆBÂVç7W&Uö66–“ÔfÇ6RÂ6÷'Eö¶W—3ÕG'VR’ÀĞ¢–çWEö÷fW'f–WrÀĞ¢§6öâæGV×2€Ğ¢°Ğ¢¢¦F–'•ö†6…÷7VÖÖ'’ÀĞ¢'&VÆöFVEögFW%÷6ÆVW÷6fR#¢&VÆöFVEögFW%÷6ÆVW÷6fRÀĞ¢&7W'&VçEö–çWEö†6‚#¢7W'&VçEö–çWEö†6‚ÀĞ¢'&Wf–÷W5ö–çWEö†6‚#¢&Wf–÷W5ö–çWEö†6‚ÀĞ¢&–çWEö†6…ö6†ævVB#¢–çWEö6†ævVBÀĞ¢&†5÷&Wf–÷W5ö–çWEö†6‚#¢&Wf–÷W5ö–çWEö†6‚—2æ÷BæöæRÀĞ¢&†5öF–'’#¢†5öF–'’ÀĞ¢&†6…ö–çWE÷7VÖÖ'’#¢÷&VF7FVEö†6…÷7VÖÖ'’†æ÷&ÖÆ—¦VEö†6…÷–ÆöB’À¢ÒÀĞ¢Vç7W&Uö66–“ÔfÇ6RÀĞ¢6÷'Eö¶W—3ÕG'VRÀĞ¢FVfVÇC×7G"ÀĞ¢’ÀĞ¢Ğ¢–bæ÷BF–'•ö–çWEöf–VÆG3 Ğ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öF–'•÷6fVBF&vWEöFFR„¥5B“ÒW2'Våö–CÒW2WFFVCÒW26¶—÷&V6öãÖæõöF–Ç•öÆörvVæW&FVE÷&÷W'F–W3ÕµÒ"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢fÇ6RÀĞ¢Ğ¢&Vg&W6†VE÷7VÖÖ'’Ò÷&Vg&W6…öF–Ç•öÆöu÷7VÖÖ'’†6öæf–rÂ7VÖÖ'’çF&vWEöFFRĞ¢&WGW&â&Vg&W6†VE÷7VÖÖ'’÷"7VÖÖ'Ğ Ğ¢–b†5öF–'’æBæ÷B–çWEö6†ævVC Ğ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öF–'•÷6¶—F&vWEöFFR„¥5B“ÒW2'Våö–CÒW26¶—÷&V6öã×Væ6†ævVEö–çWB7W'&VçEö–çWEö†6ƒÒW2&Wf–÷W5ö–çWEö†6ƒÒW2–çWEö†6…ö6†ævVCÒW2–çWE÷7VÖÖ'“ÒW2"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢7W'&VçEö–çWEö†6‚ÀĞ¢&Wf–÷W5ö–çWEö†6‚ÀĞ¢–çWEö6†ævVBÀĞ¢§6öâæGV×2†F–'•ö†6…÷7VÖÖ'’ÂVç7W&Uö66–“ÔfÇ6RÂ6÷'Eö¶W—3ÕG'VR’ÀĞ¢Ğ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öF–'•÷6fVBF&vWEöFFR„¥5B“ÒW2'Våö–CÒW2WFFVCÒW26¶—÷&V6öã×Væ6†ævVEö–çWBvVæW&FVE÷&÷W'F–W3ÕµÒ"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢fÇ6RÀĞ¢Ğ¢&Vg&W6†VE÷7VÖÖ'’Ò÷&Vg&W6…öF–Ç•öÆöu÷7VÖÖ'’†6öæf–rÂ7VÖÖ'’çF&vWEöFFRĞ¢&WGW&â&Vg&W6†VE÷7VÖÖ'’÷"7VÖÖ'Ğ Ğ¢vVæW&FVEöF–'’ÒvVæW&FUöF–'•ög&öÕöF–Ç•öÆör†F–'•ö–çWEöf–VÆG2Â7VÖÖ'’çF&vWEöFFRĞ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öF–'•övVæW&FVBF&vWEöFFR„¥5B“ÒW2'Våö–CÒW2vVæW&FVE÷&÷W'F–W3ÒW26†'3ÒW2"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢²&F–'’%ÒÀĞ¢ÆVâ†vVæW&FVEöF–'’ç7G&—‚’’ÀĞ¢Ğ¢6fU÷&W7VÇBÒ÷6fUöF–Ç•öÆöuöf–VÆG2€Ğ¢6öæf–rÀĞ¢F&vWEöFFS×7VÖÖ'’çF&vWEöFFRÀĞ¢–ÆöC×°Ğ¢&F–'’#¢vVæW&FVEöF–'’ÀĞ¢&F–'•ö–çWEö†6‚#¢7W'&VçEö–çWEö†6‚ÀĞ¢&F–'•övVæW&FVEöB#¢÷WF5÷F–ÖW7F×‚’ÀĞ¢ÒÀĞ¢Ğ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öF–'•÷6fVBF&vWEöFFR„¥5B“ÒW2'Våö–CÒW2WFFVCÒW2&V6öãÒW2vVæW&FVE÷&÷W'F–W3ÒW27W'&VçEö–çWEö†6ƒÒW2&Wf–÷W5ö–çWEö†6ƒÒW2–çWEö†6…ö6†ævVCÒW2"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢6fU÷&W7VÇBævWB‚'WFFVB"’ÀĞ¢6fU÷&W7VÇBævWB‚'&V6öâ"’ÀĞ¢²&F–'’%ÒÀĞ¢7W'&VçEö–çWEö†6‚ÀĞ¢&Wf–÷W5ö–çWEö†6‚ÀĞ¢–çWEö6†ævVBÀĞ¢Ğ¢–bfö–6Uöæ÷FW3 Ğ¢Ö&µ÷fö–6UöF–'•öæ÷FW5÷W6VB‡fö–6Uöæ÷FW2ÂF–Ç•öÆöu÷vUö–C×7VÖÖ'’çvUö–BĞ¢&Vg&W6†VE÷7VÖÖ'’Ò÷&Vg&W6…öF–Ç•öÆöu÷7VÖÖ'’†6öæf–rÂ7VÖÖ'’çF&vWEöFFRĞ¢&WGW&â&Vg&W6†VE÷7VÖÖ'’÷"7VÖÖ'Ğ Ğ Ğ¦FVböæ÷F–g•÷†6Uö2€Ğ¢6öæf–s¢6öæf–rÀĞ¢¢ÀĞ¢7VÖÖ'“¢$F–Ç”Æöu7VÖÖ'’"ÀĞ¢'Våö–C¢7G"ÀĞ¢’Óâ&ööÂÂF–7E·7G"Âç•Ó Ğ¢Æövv–æræ–æfò‚'†6Uö5öæ÷F–g•÷7F'BF&vWEöFFR„¥5B“ÒW2'Våö–CÒW2"Â7VÖÖ'’çF&vWEöFFRÂ'Våö–BĞ¢vU÷W&ÂÒ‡7VÖÖ'’çvU÷W&Â÷"""’ç7G&—‚Ğ¢–bæ÷BvU÷W&Ã Ğ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öæ÷F–g•÷6¶—VBF&vWEöFFR„¥5B“ÒW2'Våö–CÒW26¶—÷&V6öãÖÖ—76–æu÷vU÷W&Â"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢Ğ¢&WGW&âfÇ6PĞ¢–bæ÷B6öæf–ræÖ–Åög&öÒ÷"æ÷B6öæf–ræÖ–Å÷Fò÷"æ÷B6öæf–rævÖ–Åö÷77v÷&C Ğ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öæ÷F–g•÷6¶—VBF&vWEöFFR„¥5B“ÒW2'Våö–CÒW26¶—÷&V6öãÖVÖ–ÅöF—6&ÆVB"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢Ğ¢&WGW&âfÇ6PĞ Ğ¢F–'•÷FW‡BÒ‡7VÖÖ'’æF–'’÷"""’ç7G&—‚Ğ¢–bæ÷BF–'•÷FW‡C Ğ¢Æövv–æræ–æfò€Ğ¢'†6Uö5öæ÷F–g•÷6¶—VBF&vWEöFFR„¥5B“ÒW2'Våö–CÒW26¶—÷&V6öãÖV×G•öF–'’"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢Ğ¢&WGW&âfÇ6PĞ Ğ¢&VæFW&VBÒ&VæFW%öF–'•öæ÷F–f–6F–öåöÖ–Â€Ğ¢F&vWEöFFS×7VÖÖ'’çF&vWEöFFRÀĞ¢F–'“ÖF–'•÷FW‡BÀĞ¢vU÷W&Ã×vU÷W&ÂÀĞ¢Ğ¢FV6—6–öâÒFV6–FUöÖ–Å÷6VæB€Ğ¢7V&¦V7C×&VæFW&VBç7V&¦V7BÀĞ¢&öG“×&VæFW&VBçÆ–å÷FW‡BÀĞ¢&Wf–÷W5ö†6ƒ×7VÖÖ'’æF–'•öæ÷F–f–6F–öåö†6‚ÀĞ¢&Wf–÷W5÷fW'6–öã×7VÖÖ'’æF–'•öæ÷F–f–6F–öå÷fW'6–öâÀĞ¢Ğ Ğ¢Æövv–æræ–æfò€Ğ¢&Ö–Å÷6VæEöFV6—6–öâF&vWEöFFSÒW2&Wf–÷W5ö†6ƒÒW2æWuö†6ƒÒW2†6…ö6†ævVCÒW26†÷VÆE÷6VæCÒW2—5÷WFFUöÖ–ÃÒW2&Wf–÷W5÷fW'6–öãÒW2æWu÷fW'6–öãÒW2"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢FV6—6–öâç&Wf–÷W5ö†6‚÷"""ÀĞ¢FV6—6–öâææWuö†6‚ÀĞ¢FV6—6–öâæ†6…ö6†ævVBÀĞ¢FV6—6–öâç6†÷VÆE÷6VæBÀĞ¢FV6—6–öâæ—5÷WFFUöÖ–ÂÀĞ¢FV6—6–öâç&Wf–÷W5÷fW'6–öâÀĞ¢FV6—6–öâææWu÷fW'6–öâÀĞ¢Ğ¢–bæ÷BFV6—6–öâç6†÷VÆE÷6VæC Ğ¢Æövv–æræ–æfò€Ğ¢&Ö–Å÷6VæE÷6¶—VB&V6öã×6ÖUö6öçFVçBF&vWEöFFSÒW2W†—7F–æuö†6ƒÒW2"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢FV6—6–öâç&Wf–÷W5ö†6‚÷"""ÀĞ¢Ğ¢&WGW&âfÇ6PĞ Ğ¢7V&¦V7BÒFV6—6–öâæÇ•÷7V&¦V7E÷&Vf—‚‡&VæFW&VBç7V&¦V7BĞ¢Ö–Åö6öæf–rÒÖ–Ä6öæf–r€Ğ¢Ö–Åög&öÓÖ6öæf–ræÖ–Åög&öÒÀĞ¢Ö–Å÷FóÖ6öæf–ræÖ–Å÷FòÀĞ¢vÖ–Åö÷77v÷&CÖ6öæf–rævÖ–Åö÷77v÷&BÀĞ¢Ö–Åö63Ö6öæf–ræÖ–Åö62ÀĞ¢Ö–Åö&63Ö6öæf–ræÖ–Åö&62ÀĞ¢Ğ¢æ÷uö§7BÒFFWF–ÖRææ÷r„¥5B’ç&WÆ6R†Ö–7&÷6V6öæCÓ’æ—6öf÷&ÖB‚Ğ Ğ¢W†V7WFU÷v—F…÷WFFUööå÷7V66W72€Ğ¢FV6—6–öãÖFV6—6–öâÀĞ¢6VæEö7F–öãÖÆÖ&F¢6VæEöÖ–Â†Ö–Åö6öæf–rÂ7V&¦V7BÂ&VæFW&VBçÆ–å÷FW‡BÂ&VæFW&VBæ‡FÖÅö&öG’’ÀĞ¢öå÷6VæE÷7V66W73ÖÆÖ&F¢÷7Eö§6öâ€Ğ¢6öæf–ræF–'•öÖ&µöæ÷F–f–VE÷W&ÂÀĞ¢°Ğ¢'F&vWEöFFR#¢7VÖÖ'’çF&vWEöFFRÀĞ¢&F–'•öæ÷F–f–6F–öåö†6‚#¢FV6—6–öâææWuö†6‚ÀĞ¢&F–'•öæ÷F–f–6F–öå÷6VçEöB#¢æ÷uö§7BÀĞ¢&F–'•öæ÷F–f–6F–öå÷fW'6–öâ#¢FV6—6–öâææWu÷fW'6–öâÀĞ¢ÒÀĞ¢6öæf–ræ&V&W%÷Fö¶VâÀĞ¢’ÀĞ¢Ğ¢Æövv–æræ–æfò‚&Ö–Å÷6VæEöW†V7WFVBF&vWEöFFSÒW2"Â7VÖÖ'’çF&vWEöFFRĞ¢&WGW&â²'6VçB#¢G'VRÂ&Ç&VG•öÖ&¶VB#¢G'VWĞĞ Ğ Ğ¦FVb'Våöæ÷F–g•öF–'’†6öæf–s¢6öæf–rÂF&vWEöFFS¢7G"Â'Våö–C¢7G"Â¢Â&6¶f–ÆÃ¢&ööÂÒfÇ6R’ÓâæöæS Ğ¢Æövv–æræ–æfò‚&æ÷F–g•öF–'•÷WFFW5ööæÇ•öæõöÖ–ÂF&vWEöFFSÒW2'Våö–CÒW2&6¶f–ÆÃÒW2"ÂF&vWEöFFRÂ'Våö–BÂ&6¶f–ÆÂĞ¢FW2Ò†6T4FW2€Ğ¢&Vg&W6…÷7VÖÖ'“Õ÷&Vg&W6…öF–Ç•öÆöu÷7VÖÖ'’ÀĞ¢'Vå÷vVF†W#Ò€Ğ¢€Ğ¢ÆÖ&F7VÖÖ'“¢Æövv–æræ–æfò€Ğ¢&&6¶f–ÆÅ÷vVF†W%÷6¶—VC×G'VRF&vWEöFFSÒW2'Våö–CÒW2"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢Ğ¢÷"7VÖÖ'Ğ¢Ğ¢–b&6¶f–ÆÀĞ¢VÇ6R†ÆÖ&F7VÖÖ'“¢övVæW&FUöæE÷6fU÷vVF†W"†6öæf–rÂ7VÖÖ'“×7VÖÖ'’Â'Våö–C×'Våö–B’Ğ¢’ÀĞ¢'VåöW‡Vç6UöcÖÆÖ&F7VÖÖ'“¢ö6ö×WFUöW‡Vç6UöeöÆW'B‡7VÖÖ'“×7VÖÖ'’Â'Våö–C×'Våö–B’ÀĞ¢'Vå÷6ÆVWÖÆÖ&F7VÖÖ'“¢övVæW&FUöæE÷6fU÷6ÆVWö–ç6–v‡G2†6öæf–rÂ7VÖÖ'“×7VÖÖ'’Â'Våö–C×'Våö–B’ÀĞ¢'Våöæ÷FW5öÆ&VÃÖÆÖ&F7VÖÖ'“¢öVç7W&Uöæ÷FW5öÆ&VÅ÷W'6—7FVB†6öæf–rÂ7VÖÖ'“×7VÖÖ'’Â'Våö–C×'Våö–B’ÀĞ¢'Våöe÷&—6³ÖÆÖ&F7VÖÖ'“¢övVæW&FUöæE÷6fUöe÷&—6²†6öæf–rÂ7VÖÖ'“×7VÖÖ'’Â'Våö–C×'Våö–B’ÀĞ¢'Vå÷FöF•öGf–6SÖÆÖ&F7VÖÖ'“¢övVæW&FUöæE÷6fU÷FöF•öGf–6R†6öæf–rÂ7VÖÖ'“×7VÖÖ'’Â'Våö–C×'Våö–B’ÀĞ¢'VåöF–'“ÖÆÖ&F7VÖÖ'“¢övVæW&FUöæE÷6fUöF–'’€Ğ¢6öæf–rÀĞ¢7VÖÖ'“×7VÖÖ'’ÀĞ¢'Våö–C×'Våö–BÀĞ¢&VÆöFVEögFW%÷6ÆVW÷6fSÕG'VRÀĞ¢’ÀĞ¢'Våöæ÷F–g“ÖÆÖ&F7VÖÖ'“¢Æövv–æræ–æfò€Ğ¢'†6Uö5öÖ–Åöæ÷F–f–6F–öå÷6¶—VB&V6öãÖF—6&ÆVEö'•öFW6–vâF&vWEöFFSÒW2'Våö–CÒW2"ÀĞ¢7VÖÖ'’çF&vWEöFFRÀĞ¢'Våö–BÀĞ¢Ğ¢÷"fÇ6RÀĞ¢Ö&µöæ÷F–f–VCÖÆÖ&FF&vWC¢÷7Eö§6öâ€Ğ¢6öæf–ræF–'•öÖ&µöæ÷F–f–VE÷W&ÂÀĞ¢²'F&vWEöFFR#¢F&vWGÒÀĞ¢6öæf–ræ&V&W%÷Fö¶VâÀĞ¢’ÀĞ¢Ğ¢'Vå÷†6Uö2†6öæf–rÂF&vWEöFFS×F&vWEöFFRÂ'Våö–C×'Våö–BÂFW3ÖFW2Ğ Ğ Ğ¦FVb'6Uö&w2‚’Óâ&w'6RäæÖW76S Ğ¢'6W"Ò&w'6Rä&wVÖVçE'6W"†FW67&—F–öãÒ%'VâF–Ç’F–'’WFöÖF–öââ"Ğ¢'6W"æFEö&wVÖVçB€Ğ¢"Ò×†6R"ÀĞ¢6†ö–6W3Ò‚&–ævW7B"Â'V&Æ—6‚"Â&æ÷F–g•öF–'’"Â&ÆÂ"’ÀĞ¢FVfVÇCÒ&ÆÂ"ÀĞ¢†VÇÒ%†6RFò'Vâ†FVfVÇC¢ÆÂ’â"ÀĞ¢Ğ¢'6W"æFEö&wVÖVçB€Ğ¢"Ò×F&vWBÖFFR"ÀĞ¢†VÇÒ%F&vWBFFR–â¥5B…•••’ÔÔÒÔDB’âFVfVÇB—2–W7FW&F“²f÷"æ÷F–g•öF–'’öÆÂ–÷R6â÷fW'&–FRf–DôD•ôEd”4UõD$tUEôÔôDSÕDôD’â"ÀĞ¢Ğ¢'6W"æFEö&wVÖVçB€Ğ¢"ÒÖ&6¶f–ÆÂ"ÀĞ¢7F–öãÒ'7F÷&U÷G'VR"ÀĞ¢†VÇÒ%'Vâæ÷F–g•öF–'’–â&6¶f–ÆÂ×6fRÖöFR†7W'&VçFÇ’6¶—2vVF†W"vVæW&F–öâ’â"ÀĞ¢Ğ¢&WGW&â'6W"ç'6Uö&w2‚Ğ Ğ Ğ¦FVbÖ–â‚’ÓâæöæS Ğ¢&w2Ò'6Uö&w2‚Ğ¢Æövv–æræ&6–46öæf–r†ÆWfVÃÖÆövv–ærä”ädòÂf÷&ÖCÒ"R†ÆWfVÆæÖR—3¢R†ÖW76vR—2"Ğ¢æVVEö–ævW7BÒ&w2ç†6R–â‚&–ævW7B"Â&ÆÂ"Ğ¢æVVE÷V&Æ—6‚Ò&w2ç†6R–â‚'V&Æ—6‚"Â&ÆÂ"Ğ¢6öæf–rÒÆöEö6öæf–r€Ğ¢æVVEöÖ–ÃÖæVVE÷V&Æ—6‚ÀĞ¢æVVE÷F6·3ÖæVVEö–ævW7BÀĞ¢Ğ¢'Våö–BÒ÷2ævWFVçb‚$t•D…T%õ%Tåô”B"Â&Æö6Â"Ğ¢F&vWEöFFRÒ&W6öÇfU÷F&vWEöFFR†W‡Æ–6—E÷F&vWEöFFSÖ&w2çF&vWEöFFRÂ†6SÖ&w2ç†6RĞ Ğ¢Æövv–æræ–æfò€Ğ¢%7F'F–ærF–Ç’¦ö"â†6SÒW2F&vWEöFFR„¥5B“ÒW2'Våö–CÒW2"ÀĞ¢&w2ç†6RÀĞ¢F&vWEöFFRÀĞ¢'Våö–BÀĞ¢Ğ¢Æövv–æræ–æfò€Ğ¢''VçF–ÖUö–FVçF—G’v÷&¶fÆ÷uöæÖSÒW2†6SÒW2v—F‡V%÷6†ÒW2'&æ6…öæÖSÒW2"ÀĞ¢÷2ævWFVçb‚$t•D…T%õtõ$´dÄõr"Â""’ÀĞ¢&w2ç†6RÀĞ¢÷2ævWFVçb‚$t•D…T%õ4„"Â""’ÀĞ¢÷2ævWFVçb‚$t•D…T%õ$TeôäÔR"Â""’ÀĞ¢Ğ Ğ¢–b&w2ç†6R–â‚&–ævW7B"Â&ÆÂ"“ Ğ¢'Våö–ævW7B†6öæf–rÂF&vWEöFFRÂ'Våö–BĞ¢–b&w2ç†6R–â‚'V&Æ—6‚"Â&ÆÂ"“ Ğ¢'Vå÷V&Æ—6‚†6öæf–rÂF&vWEöFFRÂ'Våö–BĞ¢–b&w2ç†6R–â‚&æ÷F–g•öF–'’"Â&ÆÂ"“ Ğ¢&6¶f–ÆÂÒ&ööÂ†vWFGG"†&w2Â&&6¶f–ÆÂ"ÂfÇ6R’Ğ¢–b&6¶f–ÆÃ Ğ¢'Våöæ÷F–g•öF–'’†6öæf–rÂF&vWEöFFRÂ'Våö–BÂ&6¶f–ÆÃÕG'VRĞ¢VÇ6S Ğ¢'Våöæ÷F–g•öF–'’†6öæf–rÂF&vWEöFFRÂ'Våö–BĞ Ğ Ğ¦–bõöæÖUõòÓÒ%õöÖ–åõò# Ğ¢Ö–â‚Ğ