from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

THRESHOLD_RANK = {"pass": 0, "warning": 1, "fail": 2}


def _should_create_issue(status: str, threshold: str) -> bool:
    normalized_threshold = threshold.strip().lower() or "warning"
    if normalized_threshold not in THRESHOLD_RANK:
        normalized_threshold = "warning"
    return THRESHOLD_RANK.get(status, 0) >= THRESHOLD_RANK[normalized_threshold]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare GitHub issue title/body for daily mail quality review.")
    parser.add_argument("--report", default="artifacts/daily_mail/quality_report.json")
    parser.add_argument("--issue-body", default="artifacts/daily_mail/issue_body.md")
    parser.add_argument("--issue-title", default="artifacts/daily_mail/issue_title.txt")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--threshold", default=os.getenv("DAILY_MAIL_QUALITY_CREATE_ISSUE_ON", "warning"))
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"daily_mail_quality_issue_skip reason=missing_report path={report_path}")
        return 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    status = str(report.get("status") or "pass").lower()
    if not _should_create_issue(status, args.threshold):
        print(f"daily_mail_quality_issue_skip status={status} threshold={args.threshold}")
        return 0

    target_date = str(report.get("target_date") or "unknown")
    title = f"[Daily Mail Quality] {target_date} needs review"
    lines = [
        f"# Daily mail quality needs review - {target_date}",
        "",
        f"- Status: `{status}`",
        f"- Errors: `{report.get('error_count', 0)}`",
        f"- Warnings: `{report.get('warning_count', 0)}`",
    ]
    if args.run_url:
        lines.append(f"- Workflow run: {args.run_url}")
    lines.extend(["", "## Issues", ""])
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        lines.extend([
            f"### `{issue.get('code')}` ({issue.get('severity')})",
            "",
            str(issue.get("message") or ""),
            "",
        ])
        suggestion = str(issue.get("suggestion") or "").strip()
        if suggestion:
            lines.extend([f"Suggested fix: {suggestion}", ""])

    lines.extend([
        "## Suggested Codex task",
        "",
        "@codex",
        "このDaily mail quality reportを確認してください。`quality_report.json` / `quality_report.md` の issue code と section presence を見て、`publish/render_mail.py`、`publish/email_templates.py`、Phase C の Today advice / Diary / Study / Sleep / Weather 連携のどこが原因か切り分けてください。必要であれば修正PRを作成してください。自動マージはしないでください。",
        "",
        "## Privacy",
        "",
        "このIssueにはメール本文全文は含めていません。",
    ])

    issue_body_path = Path(args.issue_body)
    issue_title_path = Path(args.issue_title)
    issue_body_path.parent.mkdir(parents=True, exist_ok=True)
    issue_title_path.parent.mkdir(parents=True, exist_ok=True)
    issue_body_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    issue_title_path.write_text(title + "\n", encoding="utf-8")
    print(f"daily_mail_quality_issue_body_prepared path={issue_body_path} target_date={target_date} status={status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
