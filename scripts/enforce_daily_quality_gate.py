from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping

STATUS_RANK = {"pass": 0, "warning": 1, "fail": 2}


def should_fail(report: Mapping[str, object], *, fail_on: str = "fail") -> bool:
    threshold = fail_on.strip().lower()
    if threshold not in {"warning", "fail"}:
        threshold = "fail"
    status = str(report.get("status") or "fail").strip().lower()
    return STATUS_RANK.get(status, STATUS_RANK["fail"]) >= STATUS_RANK[threshold]


def _append_step_summary(markdown: str) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(markdown.rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail the final Daily workflow when its redacted quality report is not acceptable.")
    parser.add_argument("--report", default="artifacts/daily_mail/quality_report.json")
    parser.add_argument("--markdown", default="artifacts/daily_mail/quality_report.md")
    parser.add_argument("--fail-on", default=os.getenv("DAILY_QUALITY_GATE_FAIL_ON", "fail"))
    args = parser.parse_args()

    report_path = Path(args.report)
    markdown_path = Path(args.markdown)
    if not report_path.exists():
        message = "# Daily quality gate\n\n- Status: `fail`\n- Issue: `quality_report_missing`\n"
        print("daily_quality_gate status=fail reason=quality_report_missing")
        _append_step_summary(message)
        return 1

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("daily_quality_gate status=fail reason=quality_report_invalid")
        _append_step_summary("# Daily quality gate\n\n- Status: `fail`\n- Issue: `quality_report_invalid`\n")
        return 1

    if not isinstance(report, dict):
        print("daily_quality_gate status=fail reason=quality_report_not_object")
        return 1

    if markdown_path.exists():
        _append_step_summary(markdown_path.read_text(encoding="utf-8"))

    status = str(report.get("status") or "fail").lower()
    failure = should_fail(report, fail_on=args.fail_on)
    print(
        "daily_quality_gate "
        f"status={status} errors={int(report.get('error_count') or 0)} "
        f"warnings={int(report.get('warning_count') or 0)} fail_on={args.fail_on} enforced_failure={str(failure).lower()}"
    )
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
