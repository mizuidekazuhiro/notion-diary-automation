from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publish.read_daily_log import read_daily_log
from publish.render_mail import render_mail
from scripts.daily_job import load_config, resolve_target_date
from scripts.daily_mail_quality import build_markdown_report, build_quality_report, write_quality_artifacts
from scripts.expense_f_aggregator import aggregate_daily_expense_f
from scripts.f_risk_state_store import FRiskStateStore


def _run_url() -> str:
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if not repository or not run_id:
        return ""
    return f"{server_url}/{repository}/actions/runs/{run_id}"


def _failure_report(*, code: str, message: str, suggestion: str, target_date: str, run_id: str, run_url: str) -> dict[str, object]:
    return {
        "status": "fail",
        "target_date": target_date,
        "run_id": run_id,
        "run_url": run_url,
        "error_count": 1,
        "warning_count": 0,
        "issues": [
            {
                "code": code,
                "severity": "error",
                "message": message,
                "suggestion": suggestion,
            }
        ],
        "metrics": {},
        "section_presence": {},
        "privacy": {"full_mail_body_saved": False, "default_body_artifacts": "disabled"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Daily Log mail quality artifacts without sending mail.")
    parser.add_argument("--target-date", default=os.getenv("DAILY_MAIL_QUALITY_TARGET_DATE", ""))
    parser.add_argument("--artifact-dir", default=os.getenv("DAILY_MAIL_ARTIFACT_DIR", "artifacts/daily_mail"))
    args = parser.parse_args()

    run_id = os.getenv("GITHUB_RUN_ID", "local")
    run_url = _run_url()
    target_date = args.target_date.strip() or resolve_target_date(explicit_target_date=None, phase="publish")
    artifact_dir = Path(args.artifact_dir)

    try:
        config = load_config(need_mail=True, need_tasks=False)
    except Exception as exc:
        report = _failure_report(
            code="quality_export_config_error",
            message=f"Could not load publish configuration for quality export: {exc}",
            suggestion="Check MAIL_FROM, MAIL_TO, GMAIL_APP_PASSWORD, DAILY_LOG_UPSERT_URL, PUBLIC_BASE_URL, and MAIL_LINK_SECRET.",
            target_date=target_date,
            run_id=run_id,
            run_url=run_url,
        )
        write_quality_artifacts(report, artifact_dir=artifact_dir)
        print(build_markdown_report(report))
        return 0

    summary = read_daily_log(
        daily_log_read_url=config.daily_log_read_url,
        target_date=target_date,
        bearer_token=config.bearer_token,
    )
    if summary is None:
        report = build_quality_report(None, mail_plain_text="", mail_html="", run_id=run_id, run_url=run_url)
        report["target_date"] = target_date
        write_quality_artifacts(report, artifact_dir=artifact_dir)
        print(build_markdown_report(report))
        return 0

    try:
        mail = render_mail(summary, expense_f_alert={"matched": False}, f_risk_alert={})
    except Exception as exc:
        report = _failure_report(
            code="quality_export_render_error",
            message=f"Could not render mail for quality export: {exc}",
            suggestion="Inspect publish/render_mail.py, publish/email_templates.py, PUBLIC_BASE_URL, and MAIL_LINK_SECRET.",
            target_date=target_date,
            run_id=run_id,
            run_url=run_url,
        )
        write_quality_artifacts(report, artifact_dir=artifact_dir)
        print(build_markdown_report(report))
        return 0

    state_store = FRiskStateStore()
    f_risk_state = state_store.get_for_date(target_date)
    try:
        expense_f_status = aggregate_daily_expense_f(target_date).data_status
    except Exception:
        # The aggregate helper already emits a bounded sanitized diagnostic.
        expense_f_status = "query_failed"
    report = build_quality_report(
        summary,
        mail_plain_text=mail.plain_text,
        mail_html=mail.html_body,
        run_id=run_id,
        run_url=run_url,
        f_risk_state=f_risk_state,
        f_risk_state_read_ok=state_store.meta.state_read_ok,
        expense_f_status_override=expense_f_status,
    )
    write_quality_artifacts(report, artifact_dir=artifact_dir)
    print(build_markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
