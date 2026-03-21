from __future__ import annotations

import argparse
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
from scripts.mood_advice_generator import generate_today_advice
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


def run_notify_diary(config: Config, target_date: str, run_id: str) -> None:
    summary = read_daily_log(
        daily_log_read_url=config.daily_log_read_url,
        target_date=target_date,
        bearer_token=config.bearer_token,
    )
    if not summary:
        logging.info(
            "Daily_Log not found; skipping notify_diary. target_date(JST)=%s run_id=%s",
            target_date,
            run_id,
        )
        return

    logging.info(
        "diary update triggered by Daily Log fields. target_date(JST)=%s run_id=%s",
        target_date,
        run_id,
    )

    history_summaries = load_recent_daily_logs(
        daily_log_read_url=config.daily_log_read_url,
        bearer_token=config.bearer_token,
        target_date=target_date,
        days=7,
    )
    sleep_signal_fields = {
        "Sleep Start": summary.sleep_start,
        "Sleep End": summary.sleep_end,
        "Sleep Duration": summary.sleep_duration_min,
        "Sleep Score": summary.sleep_score,
        "Sleep Source": summary.sleep_source,
        "Sleep Heart Rate": summary.sleep_heart_rate,
        "Deep Duration": summary.deep_duration_min,
        "REM Duration": summary.rem_duration_min,
        "Readiness Stars": summary.readiness_stars,
        "Readiness HRV": summary.readiness_hrv,
        "Readiness BPM": summary.readiness_bpm,
        "Baseline HRV": summary.baseline_hrv,
        "Baseline Waking BPM": summary.baseline_waking_bpm,
    }
    available_sleep_inputs = sorted(
        name for name, value in sleep_signal_fields.items() if value not in (None, "")
    )
    logging.info(
        "Sleep input availability. target_date(JST)=%s run_id=%s available=%s",
        target_date,
        run_id,
        available_sleep_inputs,
    )

    sleep_payload = maybe_generate_sleep_insights(
        target_date=target_date,
        today_summary=summary,
        history_summaries=history_summaries,
    )
    if sleep_payload:
        save_result = post_json(
            config.diary_generate_url,
            {"target_date": summary.target_date, **sleep_payload},
            config.bearer_token,
        )
        logging.info(
            "Sleep insights saved via worker endpoint. target_date(JST)=%s run_id=%s updated=%s reason=%s properties=%s",
            summary.target_date,
            run_id,
            save_result.get("updated"),
            save_result.get("reason"),
            sorted(sleep_payload.keys()),
        )
        refreshed_summary = read_daily_log(
            daily_log_read_url=config.daily_log_read_url,
            target_date=target_date,
            bearer_token=config.bearer_token,
        )
        if refreshed_summary:
            summary = refreshed_summary

    if not (summary.today_advice or "").strip():
        try:
            advice_result = generate_today_advice(
                daily_log_read_url=config.daily_log_read_url,
                bearer_token=config.bearer_token,
                target_date=summary.target_date,
            )
        except Exception:
            logging.exception(
                "Failed to generate Today advice. target_date(JST)=%s run_id=%s",
                summary.target_date,
                run_id,
            )
            advice_result = None

        if advice_result and advice_result.today_advice.strip():
            save_result = post_json(
                config.diary_generate_url,
                {"target_date": summary.target_date, "today_advice": advice_result.today_advice},
                config.bearer_token,
            )
            logging.info(
                "Today advice saved via worker endpoint. target_date(JST)=%s run_id=%s updated=%s reason=%s history_days=%s high_samples=%s low_samples=%s",
                summary.target_date,
                run_id,
                save_result.get("updated"),
                save_result.get("reason"),
                advice_result.history_count,
                advice_result.high_mood_sample_count,
                advice_result.low_mood_sample_count,
            )
            refreshed_summary = read_daily_log(
                daily_log_read_url=config.daily_log_read_url,
                target_date=target_date,
                bearer_token=config.bearer_token,
            )
            if refreshed_summary:
                summary = refreshed_summary

    diary_input_fields, skipped_fields, input_overview = build_diary_input_fields(summary)
    if not diary_input_fields:
        logging.info(
            "No diary input fields available; skipping notify_diary. target_date(JST)=%s run_id=%s",
            target_date,
            run_id,
        )
        return

    diary_text = (summary.diary or "").strip()
    if not diary_text:
        logging.info(
            "Diary generation input fields: %s",
            list(diary_input_fields.keys()),
        )
        logging.info(
            "Diary generation skipped empty fields: %s",
            skipped_fields,
        )
        logging.info(
            "Diary generation input overview: %s",
            input_overview,
        )
        logging.info(
            "Generating diary from Daily Log properties via Python OpenAI... target_date(JST)=%s run_id=%s model=%s",
            summary.target_date,
            run_id,
            config.openai_model,
        )
        try:
            generated_diary = generate_diary_from_daily_log(
                diary_input_fields,
                summary.target_date,
            )
        except Exception:
            logging.exception(
                "Failed to generate diary from Daily Log properties via Python OpenAI... target_date(JST)=%s run_id=%s model=%s",
                summary.target_date,
                run_id,
                config.openai_model,
            )
            return

        save_result = post_json(
            config.diary_generate_url,
            {"target_date": summary.target_date, "diary": generated_diary},
            config.bearer_token,
        )
        logging.info(
            "Diary saved via worker endpoint... target_date(JST)=%s run_id=%s updated=%s reason=%s",
            summary.target_date,
            run_id,
            save_result.get("updated"),
            save_result.get("reason"),
        )
        refreshed_summary = read_daily_log(
            daily_log_read_url=config.daily_log_read_url,
            target_date=target_date,
            bearer_token=config.bearer_token,
        )
        if refreshed_summary:
            summary = refreshed_summary
            diary_text = (summary.diary or "").strip()

    if not diary_text:
        logging.warning(
            "Diary is empty after Python generation; skipping notify_diary... target_date(JST)=%s run_id=%s",
            target_date,
            run_id,
        )
        return

    if summary.diary_notification_sent is True:
        logging.info(
            "Diary already notified; skipping notify_diary. target_date(JST)=%s run_id=%s",
            target_date,
            run_id,
        )
        return

    page_url = (summary.page_url or "").strip()
    if not page_url:
        logging.warning(
            "Missing page_url; skipping notify_diary. target_date(JST)=%s run_id=%s",
            target_date,
            run_id,
        )
        return

    logging.info("phase_c_email_disabled")

    post_json(
        config.diary_mark_notified_url,
        {"target_date": summary.target_date},
        config.bearer_token,
    )
    logging.info(
        "Diary notified and marked. target_date(JST)=%s run_id=%s",
        summary.target_date,
        run_id,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily diary automation.")
    parser.add_argument(
        "--phase",
        choices=("ingest", "publish", "notify_diary", "all"),
        default="all",
        help="Phase to run (default: all).",
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
    target_date = get_target_date()

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
