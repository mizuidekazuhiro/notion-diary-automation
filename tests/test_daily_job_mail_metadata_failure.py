from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from scripts import daily_job


def test_run_publish_logs_metadata_failure_when_save_raises(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    summary = SimpleNamespace(
        target_date="2026-03-10",
        page_url="https://example.com",
        diary="x",
        today_advice="advice",
        weather_summary=None,
        weather_code=None,
        weather_temp_max_c=None,
        weather_temp_min_c=None,
        weather_precip_probability_max=None,
        meal_photos=[],
        location_summary=None,
        location_summary_source="empty",
        meal_photo_source_extraction_failed_count=0,
        diary_notification_sent=False,
        mail_input_hash=None,
        mail_version=None,
    )
    cfg = SimpleNamespace(
        mail_from="a",
        mail_to="b",
        gmail_app_password="c",
        mail_cc=None,
        mail_bcc=None,
        bearer_token=None,
        daily_log_read_url="https://example.com/api/daily_log",
    )
    monkeypatch.setattr(daily_job, "read_daily_log", lambda **kwargs: summary)
    monkeypatch.setattr(daily_job, "_compute_expense_f_alert", lambda **kwargs: {"matched": False})
    monkeypatch.setattr(daily_job, "_compute_f_risk_alert_runtime", lambda *args, **kwargs: {"matched": False})
    monkeypatch.setattr(daily_job, "render_mail", lambda *args, **kwargs: SimpleNamespace(subject="s", plain_text="p", html_body="h"))
    monkeypatch.setattr(daily_job, "send_mail", lambda *args, **kwargs: None)
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("save_failed")))
    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError):
        daily_job.run_publish(cfg, "2026-03-10", "r1")
    assert "mail_sent_but_metadata_persist_failed=true" in caplog.text
    assert "save_failed" in caplog.text
