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
from scripts.expense_f_aggregator import aggregate_daily_expense_f
from scripts.f_risk_state_store import FRiskStateStore

JST = ZoneInfo("Asia/Tokyo")
REQUIRED_DIARY_FIELDS = (
    "diary",
    "diary_generated_at",
)
MAJOR_HEALTH_FIELDS = (
    "resolved_sleep_duration_min",
    "sleep_score",
    "readiness_hrv",
    "readiness_bpm",
    "kcal",
    "protein",
    "fat",
    "carb",
)
VALID_EXPENSE_F_STATUSES = {"ok", "no_results"}
NONBLOCKING_HEALTH_STATUSES = {"no_data", "stale", "degraded"}


@dataclass(frozen=True)
class DailyLogQuality:
    classification: str
    missing_fields: tuple[str, ...]
    content_complete: bool
    source_complete: bool
    analysis_complete: bool

    @property
    def fully_complete(self) -> bool:
        return (
            self.content_complete
            and self.source_complete
            and self.analysis_complete
        )

    @property
    def source_missing_fields(self) -> tuple[str, ...]:
        return tuple(
            item for item in self.missing_fields if item.startswith("source:")
        )

    @property
    def blocking_source_fields(self) -> tuple[str, ...]:
        blocking: list[str] = []
        for item in self.source_missing_fields:
            if item.startswith("source:health:"):
                status = item.rsplit(":", 1)[-1]
                if status in NONBLOCKING_HEALTH_STATUSES:
                    continue
            if item == "source:unassessed":
                continue
            blocking.append(item)
        return tuple(blocking)


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
    content_incomplete_count: int = 0
    source_missing_count: int = 0
    analysis_incomplete_count: int = 0
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
        f"content:{field_name}"
        for field_name in REQUIRED_DIARY_FIELDS
        if not _is_present(getattr(summary, field_name, None))
    ]


def _health_quality_missing_fields(summary: DailyLogSummary) -> list[str]:
    explicit_status = str(getattr(summary, "health_status", "") or "").strip().lower()
    if explicit_status in {"no_data", "stale", "degraded", "failed"}:
        return [f"source:health:{explicit_status}"]

    health_data_date = str(getattr(summary, "health_data_date", "") or "").strip()
    target_date = str(getattr(summary, "target_date", "") or "").strip()
    if health_data_date and target_date and health_data_date != target_date:
        return ["source:health:stale"]

    if not any(
        hasattr(summary, name)
        for name in (*MAJOR_HEALTH_FIELDS, "sleep_duration_min")
    ):
        return []

    available_count = sum(
        1 for name in MAJOR_HEALTH_FIELDS if _is_present(getattr(summary, name, None))
    )
    # Older Daily Log payloads expose sleep_duration_min but not the resolved
    # field. Count it as the same major signal without double counting.
    if (
        not _is_present(getattr(summary, "resolved_sleep_duration_min", None))
        and _is_present(getattr(summary, "sleep_duration_min", None))
    ):
        available_count += 1
    if available_count == 0:
        return ["source:health:no_data"]
    if available_count / len(MAJOR_HEALTH_FIELDS) < 0.5:
        return ["source:health:degraded"]
    return []


def _f_risk_missing_fields(
    f_risk_state: object,
    *,
    state_read_ok: bool | None,
) -> list[str]:
    if state_read_ok is False:
        return ["analysis:f_risk:state_read_failed"]
    if not isinstance(f_risk_state, dict) or not f_risk_state:
        return ["analysis:f_risk:state_missing"]

    missing: list[str] = []
    status = str(f_risk_state.get("data_status") or "missing").strip().lower()
    if status != "ok":
        missing.append(f"analysis:f_risk:{status}")
    if bool(f_risk_state.get("fallback_used")):
        missing.append("analysis:f_risk:fallback_used")
    if not _is_present(f_risk_state.get("input_hash")):
        missing.append("analysis:f_risk:input_hash_missing")
    if not _is_present(f_risk_state.get("generated_at")):
        missing.append("analysis:f_risk:generated_at_missing")
    return missing


def evaluate_daily_log(
    summary: DailyLogSummary | None,
    *,
    expense_f_status: str | None = None,
    f_risk_state: dict[str, object] | None = None,
    f_risk_state_read_ok: bool | None = None,
    assess_external_quality: bool = False,
) -> DailyLogQuality:
    if summary is None:
        return DailyLogQuality(
            classification="missing",
            missing_fields=(
                "content:daily_log",
                "source:unassessed",
                "analysis:unassessed",
            ),
            content_complete=False,
            source_complete=False,
            analysis_complete=False,
        )

    missing = missing_diary_fields(summary)
    missing.extend(_health_quality_missing_fields(summary))

    resolved_expense_status = (
        str(expense_f_status or "").strip().lower()
        or str(getattr(summary, "expense_f_data_status", "") or "").strip().lower()
    )
    if assess_external_quality and resolved_expense_status not in VALID_EXPENSE_F_STATUSES:
        missing.append(f"source:expense_f:{resolved_expense_status or 'missing'}")
    elif resolved_expense_status and resolved_expense_status not in VALID_EXPENSE_F_STATUSES:
        missing.append(f"source:expense_f:{resolved_expense_status}")

    if not _is_present(summary.today_advice):
        missing.append("analysis:today_advice")
    if assess_external_quality:
        missing.extend(
            _f_risk_missing_fields(
                f_risk_state,
                state_read_ok=f_risk_state_read_ok,
            )
        )

    # Stable order and de-duplication keep artifacts easy to diff.
    missing = list(dict.fromkeys(missing))
    content_complete = not any(item.startswith("content:") for item in missing)
    source_complete = not any(item.startswith("source:") for item in missing)
    analysis_complete = not any(item.startswith("analysis:") for item in missing)
    classification = (
        "complete"
        if content_complete and source_complete and analysis_complete
        else "incomplete"
    )
    return DailyLogQuality(
        classification=classification,
        missing_fields=tuple(missing),
        content_complete=content_complete,
        source_complete=source_complete,
        analysis_complete=analysis_complete,
    )


def classify_daily_log(summary: DailyLogSummary | None) -> tuple[str, list[str]]:
    """Backward-compatible classification helper for callers without live sources."""
    quality = evaluate_daily_log(summary)
    return quality.classification, list(quality.missing_fields)


def _read_expense_f_status(target_date: str) -> str:
    try:
        return str(aggregate_daily_expense_f(target_date).data_status or "query_failed")
    except Exception as exc:  # noqa: BLE001
        logging.warning(
            "repair_expense_f_quality_failed target_date=%s exception_class=%s",
            target_date,
            exc.__class__.__name__,
        )
        return "query_failed"


def _read_f_risk_state(target_date: str) -> tuple[dict[str, object], bool]:
    try:
        store = FRiskStateStore()
        row = store.get_for_date(target_date)
        return (row if isinstance(row, dict) else {}), store.meta.state_read_ok
    except Exception as exc:  # noqa: BLE001
        logging.warning(
            "repair_f_risk_state_read_failed target_date=%s exception_class=%s",
            target_date,
            exc.__class__.__name__,
        )
        return {}, False


def _read_quality(summary: DailyLogSummary | None, target_date: str) -> DailyLogQuality:
    if summary is None:
        return evaluate_daily_log(None)
    expense_f_status = _read_expense_f_status(target_date)
    f_risk_state, state_read_ok = _read_f_risk_state(target_date)
    return evaluate_daily_log(
        summary,
        expense_f_status=expense_f_status,
        f_risk_state=f_risk_state,
        f_risk_state_read_ok=state_read_ok,
        assess_external_quality=True,
    )


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
    quality: DailyLogQuality,
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
                content_complete=quality.content_complete,
                source_complete=quality.source_complete,
                analysis_complete=quality.analysis_complete,
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
            content_complete=quality.content_complete,
            source_complete=quality.source_complete,
            analysis_complete=quality.analysis_complete,
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
            quality = _read_quality(summary, target_date)
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
        classification = quality.classification
        missing = list(quality.missing_fields)
        content_complete = quality.content_complete
        source_complete = quality.source_complete
        analysis_complete = quality.analysis_complete

        if not content_complete:
            stats.content_incomplete_count += 1
        if not source_complete and "source:unassessed" not in missing:
            stats.source_missing_count += 1
        if not analysis_complete:
            stats.analysis_incomplete_count += 1

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
                    quality=quality,
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

        repair_required = (
            classification == "missing"
            or not content_complete
            or not analysis_complete
            or bool(quality.blocking_source_fields)
        )
        if not repair_required:
            logging.warning(
                "status=source_missing target_date=%s missing_sources=%s repair_skipped=true processing_continues=true",
                target_date,
                list(quality.source_missing_fields),
            )
            if send_mail:
                _process_mail(
                    target_date=target_date,
                    page_id=page_id,
                    base_status="source_missing_mail_processed",
                    missing_fields=missing,
                    quality=quality,
                    stats=stats,
                )
            else:
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
            continue

        remaining_fields: list[str] = missing
        verified_quality = quality
        try:
            _repair_day(target_date)
            verified_summary = read_daily_log(
                daily_log_read_url=config.daily_log_read_url,
                target_date=target_date,
                bearer_token=config.bearer_token,
            )
            verified_quality = _read_quality(verified_summary, target_date)
            remaining_fields = list(verified_quality.missing_fields)
            verification_failed = (
                not verified_quality.content_complete
                or not verified_quality.analysis_complete
                or bool(verified_quality.blocking_source_fields)
            )
            if verification_failed:
                raise RuntimeError(
                    "repair verification failed: "
                    f"target_date={target_date} "
                    f"classification={verified_quality.classification} "
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
                    content_complete=verified_quality.content_complete,
                    source_complete=verified_quality.source_complete,
                    analysis_complete=verified_quality.analysis_complete,
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
        repaired_with_source_missing = not verified_quality.source_complete
        success_status = (
            "repair_success_source_missing"
            if repaired_with_source_missing
            else "repair_success"
        )
        if repaired_with_source_missing and (
            source_complete or "source:unassessed" in missing
        ):
            stats.source_missing_count += 1
        if send_mail:
            _process_mail(
                target_date=target_date,
                page_id=verified_page_id,
                base_status=f"{success_status}_mail_processed",
                missing_fields=list(verified_quality.missing_fields),
                quality=verified_quality,
                stats=stats,
            )
        else:
            stats.results.append(
                BackfillDayResult(
                    target_date=target_date,
                    status=success_status,
                    page_id=verified_page_id,
                    missing_fields=list(verified_quality.missing_fields),
                    content_complete=verified_quality.content_complete,
                    source_complete=verified_quality.source_complete,
                    analysis_complete=verified_quality.analysis_complete,
                )
            )
            logging.info(
                "status=%s target_date=%s source_missing=%s processing_continues=true",
                success_status,
                target_date,
                list(verified_quality.source_missing_fields),
            )

    logging.info(
        "backfill_summary scan_count=%s missing_count=%s incomplete_count=%s complete_count=%s content_incomplete_count=%s source_missing_count=%s analysis_incomplete_count=%s repaired_count=%s failed_count=%s dry_run_count=%s mail_processed_count=%s mail_failed_count=%s",
        stats.scan_count,
        stats.missing_count,
        stats.incomplete_count,
        stats.complete_count,
        stats.content_incomplete_count,
        stats.source_missing_count,
        stats.analysis_incomplete_count,
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
        f"- content_incomplete_count: {stats.content_incomplete_count}",
        f"- source_missing_count: {stats.source_missing_count}",
        f"- analysis_incomplete_count: {stats.analysis_incomplete_count}",
        f"- repaired_count: {stats.repaired_count}",
        f"- failed_count: {stats.failed_count}",
        f"- dry_run_count: {stats.dry_run_count}",
        f"- mail_processed_count: {stats.mail_processed_count}",
        f"- mail_failed_count: {stats.mail_failed_count}",
        "",
        "| date | status | content | source | analysis | mail | missing fields |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    lines.extend(
        f"| {result.target_date} | {result.status} | {result.content_complete} | {result.source_complete} | {result.analysis_complete} | {result.mail_status} | {', '.join(result.missing_fields)} |"
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
        help="Explicit manual opt-in: publish one historical diary email per complete or repaired date using normal dedupe rules.",
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
