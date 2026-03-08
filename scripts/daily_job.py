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
from publish.render_diary_notification_mail import render_diary_notification_mail
from publish.render_mail import render_mail
from publish.send_mail import MailConfig, send_mail

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

    notes_text = (summary.notes or "").strip()
    if not notes_text:
        logging.info(
            "Notes is empty; skipping notify_diary. target_date(JST)=%s run_id=%s",
            target_date,
            run_id,
        )
        return

    diary_text = (summary.diary or "").strip()
    if not diary_text:
        generate_result = post_json(
            config.diary_generate_url,
            {"target_date": summary.target_date},
            config.bearer_token,
        )
        logging.info(
            "Requested diary generation from Notes. target_date(JST)=%s run_id=%s generated=%s reason=%s",
            summary.target_date,
            run_id,
            generate_result.get("generated"),
            generate_result.get("reason"),
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
            "Diary is empty after generation; skipping notify_diary. target_date(JST)=%s run_id=%s",
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

    mail = render_diary_notification_mail(
        target_date=summary.target_date,
        diary=diary_text,
        page_url=page_url,
    )
    mail_config = MailConfig(
        mail_from=config.mail_from,
        mail_to=config.mail_to,
        gmail_app_password=config.gmail_app_password,
    )
    send_mail(mail_config, mail.subject, mail.plain_text, mail.html_body)

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
        need_mail=(need_publish or need_notify_diary),
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
