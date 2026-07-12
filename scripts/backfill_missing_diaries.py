from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publish.read_daily_log import read_daily_log
from scripts.daily_job import load_config

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class BackfillStats:
    scan_count: int = 0
    existing_count: int = 0
    missing_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    dry_run_count: int = 0


def generate_target_dates(*, end_date: str, days: int) -> list[str]:
    if days <= 0:
        raise ValueError("--days must be greater than 0")
    try:
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("--end-date must be YYYY-MM-DD") from exc
    start = end - timedelta(days=days - 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(days)]


def default_end_date(now: datetime | None = None) -> str:
    now = now or datetime.now(JST)
    return (now.astimezone(JST).date() - timedelta(days=1)).isoformat()


def _run_command(args: list[str], *, target_date: str) -> None:
    logging.info("backfill_command_start target_date=%s command=%s", target_date, " ".join(args))
    subprocess.run(args, cwd=REPO_ROOT, check=True)


def run_backfill(*, days: int, end_date: str | None, dry_run: bool) -> BackfillStats:
    resolved_end_date = end_date or default_end_date()
    target_dates = generate_target_dates(end_date=resolved_end_date, days=days)
    config = load_config(need_mail=False, need_tasks=not dry_run)
    stats = BackfillStats(scan_count=len(target_dates))
    run_id = os.getenv("GITHUB_RUN_ID", "local")

    for target_date in target_dates:
        try:
            summary = read_daily_log(
                daily_log_read_url=config.daily_log_read_url,
                target_date=target_date,
                bearer_token=config.bearer_token,
            )
        except Exception as exc:  # noqa: BLE001
            stats.failed_count += 1
            logging.exception(
                "status=check_failed target_date=%s exception_class=%s exception_message=%s",
                target_date,
                exc.__class__.__name__,
                str(exc),
            )
            continue

        if summary:
            stats.existing_count += 1
            logging.info("status=existing_skipped target_date=%s page_id=%s", target_date, getattr(summary, "page_id", ""))
            continue

        stats.missing_count += 1
        if dry_run:
            stats.dry_run_count += 1
            logging.info("status=dry_run_missing target_date=%s", target_date)
            continue

        logging.info("status=missing_detected target_date=%s", target_date)
        try:
            _run_command([sys.executable, "scripts/daily_job.py", "--phase", "ingest", "--target-date", target_date], target_date=target_date)
            _run_command([sys.executable, "apps/location_summary_writer/src/main.py", "--target-date", target_date], target_date=target_date)
            _run_command([sys.executable, "scripts/daily_job.py", "--phase", "notify_diary", "--target-date", target_date, "--backfill"], target_date=target_date)
        except Exception as exc:  # noqa: BLE001
            stats.failed_count += 1
            logging.exception(
                "status=backfill_failed target_date=%s exception_class=%s exception_message=%s",
                target_date,
                exc.__class__.__name__,
                str(exc),
            )
            continue
        stats.success_count += 1
        logging.info("status=backfill_success target_date=%s", target_date)

    logging.info(
        "backfill_summary scan_count=%s existing_count=%s missing_count=%s success_count=%s failed_count=%s dry_run_count=%s",
        stats.scan_count,
        stats.existing_count,
        stats.missing_count,
        stats.success_count,
        stats.failed_count,
        stats.dry_run_count,
    )
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill missing Daily Log pages for recent diary dates.")
    parser.add_argument("--days", type=int, default=7, help="Number of days to scan, ending at --end-date (default: 7).")
    parser.add_argument("--end-date", help="Last diary date to scan in JST (YYYY-MM-DD). Defaults to yesterday in JST.")
    parser.add_argument("--dry-run", action="store_true", help="Only check and log missing/existing status; do not update Notion or call OpenAI.")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    try:
        stats = run_backfill(days=args.days, end_date=args.end_date, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    if stats.failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
