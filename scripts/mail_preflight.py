from __future__ import annotations

import hashlib
import os
from typing import Any

from scripts.daily_mail_quality import build_quality_report, write_quality_artifacts
from publish.source_disclosures import source_disclosures


def rendered_hash(mail: Any) -> str:
    return hashlib.sha256((mail.subject + "\n" + mail.plain_text + "\n" + mail.html_body).encode()).hexdigest()


def preflight_report(summary: Any, mail: Any, *, expense_f: dict, f_risk: dict, run_id: str) -> dict:
    report = build_quality_report(summary, mail_plain_text=mail.plain_text, mail_html=mail.html_body,
        run_id=run_id, f_risk_state=f_risk,
        expense_f_status_override=expense_f.get("data_status"))
    disclosed = {kind for kind, text in source_disclosures(summary, expense_f, f_risk)
                 if text in mail.plain_text and text in mail.html_body}
    for issue in report["issues"]:
        code = issue["code"]
        if (("expense_f" in disclosed and code in {"expense_f_status_missing", "expense_f_unavailable"})
                or ("f_risk" in disclosed and code in {"f_risk_state_read_failed", "f_risk_state_missing", "f_risk_not_ok", "f_risk_fallback_used"})):
            issue.update(severity="warning", explicitly_disclosed=True)
    report["error_count"] = sum(issue["severity"] == "error" for issue in report["issues"])
    report["warning_count"] = sum(issue["severity"] == "warning" for issue in report["issues"])
    report["status"] = "fail" if report["error_count"] else "warning" if report["warning_count"] else "pass"
    report.update(inspection_stage="before_send", checked_render_hash=rendered_hash(mail),
                  delivery_status="not_started", metadata_status="not_attempted", post_delivery_check="not_attempted")
    return report


def save_delivery_report(report: dict, **updates: Any) -> None:
    report.update(updates)
    if (report.get("metadata_status") in {"failed", "unverified"} or report.get("receipt_status") == "failed"
            or report.get("delivery_status") == "uncertain"):
        issues = report.setdefault("issues", [])
        if not any(issue.get("code") == "delivery_state_needs_review" for issue in issues):
            issues.append({"code": "delivery_state_needs_review", "severity": "warning",
                           "message": "配信結果と、その後の状態保存・確認を分けて確認してください。自動再送は行いません。"})
            report["warning_count"] = int(report.get("warning_count", 0)) + 1
            if not report.get("error_count"):
                report["status"] = "warning"
    write_quality_artifacts(report, artifact_dir=os.getenv("DAILY_MAIL_ARTIFACT_DIR", "artifacts/daily_mail"))
