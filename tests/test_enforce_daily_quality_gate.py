from __future__ import annotations

import json

from scripts import enforce_daily_quality_gate as gate


def test_should_fail_on_fail_but_not_warning() -> None:
    assert gate.should_fail({"status": "fail"}, fail_on="fail") is True
    assert gate.should_fail({"status": "warning"}, fail_on="fail") is False


def test_main_fails_when_report_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["gate", "--report", str(tmp_path / "missing.json")])
    assert gate.main() == 1


def test_main_appends_summary_and_fails_after_mail_report(tmp_path, monkeypatch) -> None:
    report = tmp_path / "quality_report.json"
    markdown = tmp_path / "quality_report.md"
    summary = tmp_path / "step_summary.md"
    report.write_text(json.dumps({"status": "fail", "error_count": 1, "warning_count": 0}), encoding="utf-8")
    markdown.write_text("# Redacted daily quality report\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr("sys.argv", ["gate", "--report", str(report), "--markdown", str(markdown)])

    assert gate.main() == 1
    assert "Redacted daily quality report" in summary.read_text(encoding="utf-8")
