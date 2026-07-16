from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publish.read_daily_log import DailyLogSummary, read_daily_log
from scripts.daily_job import load_config

JST = ZoneInfo("Asia/Tokyo")
REQUIRED_PHASE_ABC_FIELDS = (
    "target_date_value",
    "activity_summary",
    "mail_id",
    "today_advice",
    "diary",
    "today_advice_generated_at",
    "diary_generated_at",
)


@dataclass
class BackfillDayResult:
    target_date: str
    status: str
    page_id: str = ""
    missing_fields: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class BackfillStats:
    scan_count: int = 0
    missing_count: int = 0
    incomplete_count: int = 0
    complete_count: int = 0
    repaired_count: int = 0
    failed_count: int = 0
    dry_run_count: int = 0
    results: list[BackfillDayResult] = field(default_factory=list)


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


def _is_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def missing_phase_abc_fields(summary: DailyLogSummary) -> list[str]:
    missing: list[str] = []
    for field_name in REQUIRED_PHASE_ABC_FIELDS:
        if not _is_present(getattr(summary, field_name, None)):
            missing.append(field_name)
    if summary.target_date != summary.date and not _is_present(summary.target_date_value):
        if "target_date_value" not in missing:
            missing.append("target_date_value")
    return missing


def classify_daily_log(summary: DailyLogSummary | None) -> tuple[str, list[str]]:
    if summary is None:
        return "missing", []
    missing = missing_phase_abc_fields(summary)
    return ("incomplete", missing) if missing else ("complete", [])


def _run_command(args: list[str], *, target_date: str) -> None:
    logging.info("backfill_command_start target_date=%s command=%s", target_date, " ".join(args))
    subprocess.run(args, cwd=REPO_ROOT, check=True)


def _repair_day(target_date: str) -> None:
    _run_command([sys.executable, "scripts/daily_job.py", "--phase", "ingest", "--target-date", target_date], target_date=target_date)
    _run_command([sys.executable, "apps/location_summary_writer/src/main.py", "--target-date", target_date], target_date=target_date)
    _run_command([sys.executable, "scripts/daily_job.py", "--phase", "notify_diary", "--target-date", target_date, "--backfill"], target_date=target_date)


def run_backfill(*, days: int, end_date: str | None, dry_run: bool) -> BackfillStats:
    resolved_end_date = end_date or default_end_date()
    target_dates = generate_target_dates(end_date=resolved_end_date, days=days)
    config = load_config(need_mail=False, need_tasks=not dry_run)
    stats = BackfillStats(scan_count=len(target_dates))

    for target_date in target_dates:
        try:
            summary = read_daily_log(daily_log_read_url=config.daily_log_read_url, target_date=target_date, bearer_token=config.bearer_token)
            classification, missing = classify_daily_log(summary)
        except Exception as exc:  # noqa: BLE001
            stats.failed_count += 1
            stats.results.append(BackfillDayResult(target_date=target_date, status="repair_failed", error=str(exc)))
            logging.exception("status=repair_failed target_date=%s exception_class=%s exception_message=%s", target_date, exc.__class__.__name__, str(exc))
            continue

        page_id = getattr(summary, "page_id", "") if summary else ""
        if classification == "complete":
            stats.complete_count += 1
            stats.results.append(BackfillDayResult(target_date=target_date, status="complete_skipped", page_id=page_id))
            logging.info("status=complete_skipped target_date=%s page_id=%s", target_date, page_id)
            continue
        if classification == "missing":
            stats.missing_count += 1
            logging.info("status=missing_detected target_date=%s", target_date)
        else:
            stats.incomplete_count += 1
            logging.info("status=incomplete_detected target_date=%s page_id=%s missing_fields=%s", target_date, page_id, ",".join(missing))

        if dry_run:
            stats.dry_run_count += 1
            stats.results.append(BackfillDayResult(target_date=target_date, status=f"dry_run_{classification}", page_id=page_id, missing_fields=missing))
            continue
        try:
            _repair_day(target_date)
        except Exception as exc:  # noqa: BLE001
            stats.failed_count += 1
            stats.results.append(BackfillDayResult(target_date=target_date, status="repair_failed", page_id=page_id, missing_fields=missing, error=str(exc)))
            logging.exception("status=repair_failed target_date=%s exception_class=%s exception_message=%s", target_date, exc.__class__.__name__, str(exc))
            continue
        stats.repaired_count += 1
        stats.results.append(BackfillDayResult(target_date=target_date, status="repair_success", page_id=page_id, missing_fields=missing))
        logging.info("status=repair_success target_date=%s", target_date)

    logging.info("backfill_summary scan_count=%s missing_count=%s incomplete_count=%s complete_count=%s repaired_count=%s failed_count=%s dry_run_count=%s", stats.scan_count, stats.missing_count, stats.incomplete_count, stats.complete_count, stats.repaired_count, stats.failed_count, stats.dry_run_count)
    return stats


def write_artifacts(stats: BackfillStats, artifact_dir: str | Path) -> None:
    out = Path(artifact_dir); out.mkdir(parents=True, exist_ok=True)
    payload = asdict(stats)
    (out / "daily_log_repair_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Daily Log repair summary", "", f"- scan_count: {stats.scan_count}", f"- missing_count: {stats.missing_count}", f"- incomplete_count: {stats.incomplete_count}", f"- complete_count: {stats.complete_count}", f"- repaired_count: {stats.repaired_count}", f"- failed_count: {stats.failed_count}", f"- dry_run_count: {stats.dry_run_count}", "", "| date | status | missing fields |", "|---|---|---|"]
    lines.extend(f"| {r.target_date} | {r.status} | {', '.join(r.missing_fields)} |" for r in stats.results)
    (out / "daily_log_repair_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair missing or incomplete Daily Log pages for recent diary dates.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--end-date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--artifact-dir", default="artifacts/daily_log_repair")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    try:
        stats = run_backfill(days=args.days, end_date=args.end_date, dry_run=args.dry_run)
        write_artifacts(stats, args.artifact_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); sys.exit(2)
    if stats.failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
