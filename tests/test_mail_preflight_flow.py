from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import daily_job
from scripts.mail_preflight import preflight_report, rendered_hash
from publish.render_mail import render_mail
from tests.test_today_advice_30d_pipeline import _summary


def setup_publish(monkeypatch, **changes):
    row = _summary(20, target_date="2026-03-20", diary="2026-03-20\n確認できる記録を振り返った。",
        today_advice="記録が不足しているため、今日のリスクは判定できません。", expense_f_data_status="no_results",
        generation_metadata='{"advice":{"status":"insufficient_data","target_date":"2026-03-21","fallback_used":false}}', **changes)
    cfg = SimpleNamespace(mail_from="sender@example.com", mail_to=["recipient@example.com"], gmail_app_password="test",
                          mail_cc=None, mail_bcc=None, bearer_token=None, daily_log_read_url="read")
    expense = {"matched": False, "data_status": "no_results"}
    risk = {"matched": False, "data_status": "ok", "input_hash": "risk-hash", "generated_at": "2026-03-21T00:00:00Z", "fallback_used": False}
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("MAIL_LINK_SECRET", "test-link-key")
    monkeypatch.setattr(daily_job, "read_daily_log", lambda **kw: row)
    monkeypatch.setattr(daily_job, "_compute_expense_f_alert", lambda **kw: expense)
    monkeypatch.setattr(daily_job, "_compute_f_risk_alert_runtime", lambda *args, **kw: risk)
    monkeypatch.setattr(daily_job, "_get_mail_metadata_persist_verify_retries", lambda: 1)
    return row, cfg, expense, risk


def test_preflight_uses_exact_sent_subject_text_html_and_survives_metadata_failure(monkeypatch):
    row, cfg, _, _ = setup_publish(monkeypatch, mail_input_hash="old-input", mail_version=1)
    inspected, sent = [], []
    def inspect(summary, mail, **kwargs):
        inspected.append(mail)
        return preflight_report(summary, mail, **kwargs)
    monkeypatch.setattr(daily_job, "preflight_report", inspect)
    monkeypatch.setattr(daily_job, "send_mail", lambda config, subject, text, html: sent.append((subject, text, html)))
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("metadata unavailable")))
    daily_job.run_publish(cfg, row.target_date, "test-run")
    assert len(sent) == 1
    assert sent[0] == (inspected[0].subject, inspected[0].plain_text, inspected[0].html_body)
    report = json.loads((Path(os.environ["DAILY_MAIL_ARTIFACT_DIR"]) / "quality_report.json").read_text(encoding="utf-8"))
    assert report["checked_render_hash"] == rendered_hash(inspected[0])
    assert report["delivery_status"] == "sent" and report["metadata_status"] == "failed"
    daily_job.run_publish(cfg, row.target_date, "retry-run")
    assert len(sent) == 1


def test_major_date_conflict_blocks_before_smtp(monkeypatch):
    row, cfg, _, _ = setup_publish(monkeypatch)
    row = replace(row, generation_metadata='{"advice":{"status":"insufficient_data","target_date":"2026-03-28"}}')
    monkeypatch.setattr(daily_job, "read_daily_log", lambda **kw: row)
    monkeypatch.setattr(daily_job, "send_mail", lambda *args: pytest.fail("date conflict must block SMTP"))
    with pytest.raises(RuntimeError, match="mail_preflight_failed"):
        daily_job.run_publish(cfg, row.target_date, "test-run")


def test_missing_f_data_is_disclosed_and_never_claimed_low_risk(monkeypatch):
    row, _, expense, risk = setup_publish(monkeypatch)
    risk.update(data_status="no_data")
    mail = render_mail(row, expense_f_alert=expense, f_risk_alert=risk)
    report = preflight_report(row, mail, expense_f=expense, f_risk=risk, run_id="test-run")
    assert "今日の支出リスク：" in mail.plain_text and "判定を控えます" in mail.plain_text
    assert "低リスク" not in mail.plain_text
    assert report["error_count"] == 0
    assert any(issue.get("explicitly_disclosed") for issue in report["issues"])


def test_unavailable_risk_can_abstain_but_successful_risk_requires_audit_metadata(monkeypatch):
    row, _, expense, _ = setup_publish(monkeypatch)
    for status, should_block in [('unavailable', False), ('ok', True)]:
        risk={'matched':False,'data_status':status}
        mail=render_mail(row,expense_f_alert=expense,f_risk_alert=risk)
        report=preflight_report(row,mail,expense_f=expense,f_risk=risk,run_id='test')
        issues=[issue for issue in report['issues'] if issue['code']=='f_risk_observability_missing']
        assert bool([issue for issue in issues if issue['severity']=='error']) is should_block
