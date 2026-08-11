from __future__ import annotations

import argparse
import json
import logging
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
REQUIRED_DIARY_FIELDS = (
    "diary",
    "diary_generated_at",
)


@dataclass
class BackfillDayResult:
    target_date: str
    status: str
    page_id: str = ""
    missing_fields: list[str] = field(default_factory=list)
    mail_status: str = ""
    error: str = ""
    content_complete: bool = False
    source_complete: bool = False
    analysis_complete: bool = False


@dataclass
class BackfillStats:
    scan_count: int = 0
    missing_count: int = 0
    incomplete_count: int = 0
    complete_count: int = 0
    repaired_count: int = 0
    failed_count: int = 0
    dry_run_count: int = 0
    mail_processed_count: int = 0
    mail_failed_count: int = 0
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


def missing_diary_fields(summary: DailyLogSummary) -> list[str]:
    """Return fields that prove the diary itself has not been generated yet.

    Activity, notes, location, meal, task, and expense fields are intentionally
    excluded because they can be legitimately empty on days with no source data.
    """
    return [
        field_name
        for field_name in REQUIRED_DIARY_FIELDS
        if not _is_present(getattr(summary, field_name, None))
    ]


def classify_daily_log(summary: DailyLogSummary | None) -> tuple[str, list[str]]:
    if summary is None:
        return "missing", []
    missing = missing_diary_fields(summary)
    health_fields = (
        "sleep_duration_min",
        "sleep_score",
        "readiness_hrv",
        "readiness_bpm",
        "kcal",
        "protein",
        "fat",
        "carb",
    )
    if any(hasattr(summary, name) for name in health_fields) and not any(_is_present(getattr(summary, name, None)) for name in health_fields):
        missing.append("source:health")
    if (getattr(summary, "expense_f_data_status", None) or "").strip() in {
        "query_failed",
        "schema_unresolved",
        "schema_unavailable",
        "expenses_data_unavailable",
    }:
        missing.append(f"source:expense_f:{getattr(summary, 'expense_f_data_status', None)}")
    if not _is_present(summary.today_advice):
        missing.append("analysis:today_advice")
    if any(hasattr(summary, name) for name in ("f_risk_generated_at", "f_risk_reason", "f_risk_input_hash")) and not any(
        _is_present(value)
        for value in (getattr(summary, "f_risk_generated_at", None), getattr(summary, "f_risk_reason", None), getattr(summary, "f_risk_input_hash", None))
    ):
        missing.append("analysis:f_risk")
    return ("incomplete", missing) if missing else ("complete", [])


def _run_command(args: list[str], *, target_date: str) -> None:
    logging.info(
        "backfill_command_start target_date=%s command=%s",
        target_date,
        " ".join(args),
    )
    subprocess.run(args, cwd=REPO_ROOT, check=True)


def _repair_day(target_date: str) -> None:
    _run_command(
        [
            sys.executable,
            "scripts/daily_job.py",
            "--phase",
            "ingest",
            "--target-date",
            target_date,
        ],
        target_date=target_date,
    )
    _run_command(
        [
            sys.executable,
            "apps/location_summary_writer/src/main.py",
            "--target-date",
            target_date,
        ],
        target_date=target_date,
    )
    _run_command(
        [
            sys.executable,
            "scripts/daily_job.py",
            "--phase",
            "notify_diary",
            "--target-date",
            target_date,
            "--backfill",
        ],
        target_date=target_date,
    )


def _publish_day(target_date: str) -> None:
    """Publish one historical diary mail using the normal dedupe rules."""
    _run_command(
        [
            sys.executable,
            "scripts/daily_job.py",
            "--phase",
            "publish",
            "--target-date",
            target_date,
        ],
        target_date=target_date,
    )


def _process_mail(
    *,
    target_date: str,
    page_id: str,
    base_status: str,
    missing_fields: list[str],
    stats: BackfillStats,
) -> None:
    try:
        _publish_day(target_date)
    except Exception as exc:  # noqa: BLE001
        stats.failed_count += 1
        stats.mail_failed_count += 1
        stats.results.append(
            BackfillDayResult(
                target_date=target_date,
                status="mail_failed",
                page_id=page_id,
                missing_fields=missing_fields,
                mail_status="failed",
                error=str(exc),
            )
        )
        logging.exception(
            "status=mail_failed target_date=%s exception_class=%s exception_message=%s",
            target_date,
            exc.__class__.__name__,
            str(exc),
        )
        return

    stats.mail_processed_count += 1
    stats.results.append(
        BackfillDayResult(
            target_date=target_date,
            status=base_status,
            page_id=page_id,
            missing_fields=missing_fields,
            mail_status="processed",
        )
    )
    logging.info(
        "status=%s target_date=%s mail_status=processed",
        base_status,
        target_date,
    )


def run_backfill(
    *,
    days: int,
    end_date: str | None,
    dry_run: bool,
    send_mail: bool = False,
) -> BackfillStats:
    resolved_end_date = end_date or default_end_date()
    target_dates = generate_target_dates(end_date=resolved_end_date, days=days)
    config = load_config(
        need_mail=send_mail and not dry_run,
        need_tasks=not dry_run,
    )
    stats = BackfillStats(scan_count=len(target_dates))

    for target_date in target_dates:
        try:
            summary = read_daily_log(
                daily_log_read_url=config.daily_log_read_url,
                target_date=target_date,
                bearer_token=config.bearer_token,
            )
            classification, missing = classify_daily_log(summary)
        except Exception as exc:  # noqa: BLE001
            stats.failed_count += 1
            stats.results.append(
                BackfillDayResult(
                    target_date=target_date,
                    status="repair_failed",
                    error=str(exc),
                )
            )
            logging.exception(
                "status=repair_failed target_date=%s exception_class=%s exception_message=%s",
                target_date,
                exc.__class__.__name__,
                str(exc),
            )
            continue

        page_id = getattr(summary, "page_id", "") if summary else ""
        content_complete = not any(item in REQUIRED_DIARY_FIELDS for item in missing)
        source_complete = not any(item.startswith("source:") for item in missing)
        analysis_complete = not any(item.startswith("analysis:") for item in missing)

        if classification == "complete":
            stats.complete_count += 1
            if dry_run:
                stats.dry_run_count += 1
                stats.results.append(
                    BackfillDayResult(
                        target_date=target_date,
                        status="dry_run_complete",
                        page_id=page_id,
                        mail_status="would_process" if send_mail else "",
                        content_complete=True,
                        source_complete=True,
                        analysis_complete=True,
                    )
                )
                continue
            if send_mail:
                _process_mail(
                    target_date=target_date,
                    page_id=page_id,
                    base_status="complete_mail_processed",
                    missing_fields=[],
                    stats=stats,
                )
            else:
                stats.results.append(
                    BackfillDayResult(
                        target_date=target_date,
                        status="complete_skipped",
                        page_id=page_id,
                        content_complete=True,
                        source_complete=True,
                        analysis_complete=True,
                    )
                )
                logging.info(
                    "status=complete_skipped target_date=%s page_id=%s",
                    target_date,
                    page_id,
                )
            continue

        if classification == "missing":
            stats.missing_count += 1
            logging.info("status=missing_detected target_date=%s", target_date)
        else:
            stats.incomplete_count += 1
            logging.info(
                "status=incomplete_detected target_date=%s page_id=%s missing_fields=%s",
                target_date,
                page_id,
                ",".join(missing),
            )

        if dry_run:
            stats.dry_run_count += 1
            dry_run_status = (
                "dry_run_missing" if classification == "missing" else "dry_run_incomplete"
            )
            logging.info(
                "status=%s target_date=%s page_id=%s missing_fields=%s",
                dry_run_status,
                target_date,
                page_id,
                ",".join(missing),
            )
            stats.results.append(
                BackfillDayResult(
                    target_date=target_date,
                    status=dry_run_status,
                    page_id=page_id,
                    missing_fields=missing,
                    mail_status="would_process" if send_mail else "",
                    content_complete=content_complete,
                    source_complete=source_complete,
                    analysis_complete=analysis_complete,
                )
            )
            continue

        if not source_complete:
            stats.results.append(
                BackfillDayResult(
                    target_date=target_date,
                    status="source_missing",
                    page_id=page_id,
                    missing_fields=missing,
                    content_complete=content_complete,
                    source_complete=False,
                    analysis_complete=analysis_complete,
                )
            )
            logging.warning(
                "status=source_missing target_date=%s missing_sources=%s repair_skipped=true",
                target_date,
                [item for item in missing if item.startswith("source:")],
            )
            continue

        remaining_fields: list[str] = missing
        try:
            _repair_day(target_date)
            verified_summary = read_daily_log(
                daily_log_read_url=config.daily_log_read_url,
                target_date=target_date,
                bearer_token=config.bearer_token,
            )
            verified_classification, remaining_fields = classify_daily_log(
                verified_summary
            )
            if verified_classification != "complete":
                raise RuntimeError(
                    "repair verification failed: "
                    f"target_date={target_date} "
                    f"classification={verified_classification} "
                    f"remaining_fields={remaining_fields}"
                )
        except Exception as exc:  # noqa: BLE001
            stats.failed_count += 1
            stats.results.append(
                BackfillDayResult(
                    target_date=target_date,
                    status="repair_failed",
                    page_id=page_id,
                    missing_fields=remaining_fields,
                    error=str(exc),
                )
            )
            logging.exception(
                "status=repair_failed target_date=%s exception_class=%s exception_message=%s",
                target_date,
                exc.__class__.__name__,
                str(exc),
            )
            continue

        stats.repaired_count += 1
        verified_page_id = (
            getattr(verified_summary, "page_id", page_id)
            if verified_summary
            else page_id
        )
        if send_mail:
            _process_mail(
                target_date=target_date,
                page_id=verified_page_id,
                base_status="repair_success_mail_processed",
                missing_fields=[],
                stats=stats,
            )
        else:
            stats.results.append(
                BackfillDayResult(
                    target_date=target_date,
                    status="repair_success",
                    page_id=verified_page_id,
                    missing_fields=[],
                )
            )
            logging.info("status=repair_success target_date=%s", target_date)

    logging.info(
        "backfill_summary scan_count=%s missing_count=%s incomplete_count=%s complete_count=%s repaired_count=%s failed_count=%s dry_run_count=%s mail_processed_count=%s mail_failed_count=%s",
        stats.scan_count,
        stats.missing_count,
        stats.incomplete_count,
        stats.complete_count,
        stats.repaired_count,
        stats.failed_count,
        stats.dry_run_count,
        stats.mail_processed_count,
        stats.mail_failed_count,
    )
    return stats


def write_artifacts(stats: BackfillStats, artifact_dir: str | Path) -> None:
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = asdict(stats)
    (out / "daily_log_repair_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Daily Log repair summary",
        "",
        f"- scan_count: {stats.scan_count}",
        f"- missing_count: {stats.missing_count}",
        f"- incomplete_count: {stats.incomplete_count}",
        f"- complete_count: {stats.complete_count}",
        f"- repaired_count: {stats.repaired_count}",
        f"- failed_count: {stats.failed_count}",
        f"- dry_run_count: {stats.dry_run_count}",
        f"- mail_processed_count: {stats.mail_processed_count}",
        f"- mail_failed_count: {stats.mail_failed_count}",
        "",
        "| date | status | mail | missing fields |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {result.target_date} | {result.status} | {result.mail_status} | {', '.join(result.missing_fields)} |"
        for result in stats.results
    )
    (out / "daily_log_repair_summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair missing or incomplete Daily Log pages for recent diary dates."
    )
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--end-date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--send-mail",
        action="store_true",
        help="Publish one historical diary email per complete or repaired date using normal dedupe rules.",
    )
    parser.add_argument("--artifact-dir", default="artifacts/daily_log_repair")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    stats = BackfillStats()
    exit_code = 0
    try:
        stats = run_backfill(
            days=args.days,
            end_date=args.end_date,
            dry_run=args.dry_run,
            send_mail=getattr(args, "send_mail", False),
        )
        if stats.failed_count > 0:
            exit_code = 1
    except ValueError as exc:
        stats.failed_count += 1
        stats.results.append(
            BackfillDayResult(
                target_date="",
                status="repair_failed",
                error=str(exc),
            )
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        exit_code = 2
    except Exception as exc:  # noqa: BLE001
        stats.failed_count += 1
        stats.results.append(
            BackfillDayResult(
                target_date="",
                status="repair_failed",
                error=str(exc),
            )
        )
        logging.exception(
            "status=repair_failed target_date= exception_class=%s exception_message=%s",
            exc.__class__.__name__,
            str(exc),
        )
        exit_code = 1
    finally:
        write_artifacts(stats, args.artifact_dir)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
