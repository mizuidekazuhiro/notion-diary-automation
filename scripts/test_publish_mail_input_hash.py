from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import daily_job


def _summary(**kwargs):
    base = dict(
        target_date="2026-04-01",
        weather_summary="晴れ",
        weather_code=1,
        weather_temp_max_c=24.0,
        weather_temp_min_c=16.0,
        weather_precip_probability_max=20.0,
        weather_location="Tokyo",
        diary="diary",
        today_advice="advice",
        sleep_analysis_jp="sleep",
        today_condition_forecast_jp="forecast",
        activity_summary="walk",
        location_summary="office",
        location_summary_source="empty",
        meal_summary="meal",
        meal_photos=[],
        meal_photo_source_extraction_failed_count=0,
        expenses_total=1000.0,
        expenses=SimpleNamespace(count=1, top=[SimpleNamespace(title="Coffee", amount=500.0, url="")]),
        done_count=1,
        done_tasks=["task"],
        done_tasks_detail=[SimpleNamespace(title="task", done_date="2026-04-01", event_date="2026-04-01")],
        drop_count=0,
        drop_tasks=[],
        kcal=2000.0,
        protein=100.0,
        fat=60.0,
        carb=250.0,
        weight=60.0,
        sleep_start="2026-03-31T23:00:00+09:00",
        sleep_end="2026-04-01T07:00:00+09:00",
        resolved_sleep_duration_min=480.0,
        resolved_sleep_duration_text="8時間",
        sleep_score=80.0,
        sleep_source="oura",
        deep_duration_min=100.0,
        rem_duration_min=90.0,
        readiness_stars=4.0,
        readiness_hrv=40.0,
        readiness_bpm=55.0,
        mail_input_hash=None,
        mail_input_snapshot_json=None,
        mail_version=None,
        page_id="test-page-id",
        diary_notification_sent=False,
        mail_id="",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _cfg():
    return daily_job.Config(mail_from="a@example.com", mail_to=["b@example.com"], gmail_app_password="x", tasks_closed_url="", daily_log_upsert_url="", daily_log_ensure_url="", health_ingest_url="", expenses_ingest_url="", daily_log_read_url="", diary_generate_url="", diary_mark_notified_url="", bearer_token=None, openai_model="")


def test_publish_first_send(monkeypatch):
    sent = []
    updated = []
    summary = _summary()
    reads=[summary, summary]
    monkeypatch.setattr("scripts.daily_job.read_daily_log", lambda **_kwargs: reads.pop(0) if reads else summary)
    monkeypatch.setattr("scripts.daily_job.render_mail", lambda *_args, **_kwargs: SimpleNamespace(subject="S", plain_text="P", html_body="H"))
    monkeypatch.setattr("scripts.daily_job._compute_expense_f_alert", lambda **_kwargs: {"summary": ""})
    monkeypatch.setattr("scripts.daily_job._compute_f_risk_alert_runtime", lambda *_args, **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: sent.append(True))
    monkeypatch.setattr("scripts.daily_job._save_daily_log_fields", lambda *_args, **kwargs: updated.append(kwargs["payload"]) or {"updated": True})
    monkeypatch.setattr("scripts.daily_job._refresh_daily_log_summary", lambda *_args, **_kwargs: SimpleNamespace(mail_input_hash=updated[-1]["mail_input_hash"], mail_version=updated[-1]["mail_version"], mail_sent_at=updated[-1]["mail_sent_at"], mail_input_snapshot_json=updated[-1]["mail_input_snapshot"]))
    daily_job.run_publish(_cfg(), "2026-04-01", "r1")
    assert len(sent) == 1
    assert len(updated) == 1
    saved_payload = updated[0]
    assert isinstance(saved_payload.get("mail_input_hash"), str) and saved_payload["mail_input_hash"]
    assert "mail_input_snapshot_json" not in saved_payload
    assert isinstance(saved_payload.get("mail_sent_at"), str) and saved_payload["mail_sent_at"]
    assert isinstance(saved_payload.get("mail_version"), int)
    assert isinstance(saved_payload.get("mail_input_snapshot"), str) and saved_payload["mail_input_snapshot"]
    assert saved_payload.get("diary_notification_sent") is True


def test_publish_skip_when_input_hash_unchanged_even_if_mail_body_changed(monkeypatch):
    sent = []
    updated = []
    summary = _summary()
    monkeypatch.setattr("scripts.daily_job.build_mail_input_snapshot", lambda *_args, **_kwargs: {"target_date": "2026-04-01"})
    same_hash = daily_job.sha256_hex('{"target_date":"2026-04-01"}')
    summary.mail_input_hash = same_hash
    reads=[summary, summary]
    monkeypatch.setattr("scripts.daily_job.read_daily_log", lambda **_kwargs: reads.pop(0) if reads else summary)
    mails = [SimpleNamespace(subject="S", plain_text="P1", html_body="H1"), SimpleNamespace(subject="S2", plain_text="P2", html_body="H2")]
    monkeypatch.setattr("scripts.daily_job.render_mail", lambda *_args, **_kwargs: mails.pop(0) if mails else SimpleNamespace(subject="S2", plain_text="P2", html_body="H2"))
    monkeypatch.setattr("scripts.daily_job._compute_expense_f_alert", lambda **_kwargs: {"summary": ""})
    monkeypatch.setattr("scripts.daily_job._compute_f_risk_alert_runtime", lambda *_args, **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: sent.append(True))
    monkeypatch.setattr("scripts.daily_job._save_daily_log_fields", lambda *_args, **kwargs: updated.append(kwargs["payload"]) or {"updated": True})
    daily_job.run_publish(_cfg(), "2026-04-01", "r1")
    assert sent == []
    assert updated == []


def test_mail_input_hash_ignores_ai_generated_text_diffs():
    s1 = _summary(
        diary="A",
        today_advice="B",
        sleep_analysis_jp="C",
        today_condition_forecast_jp="D",
        diary_input_hash="diary-hash",
        today_advice_input_hash="advice-hash",
        weather_input_hash="weather-hash",
    )
    s2 = _summary(
        diary="A changed",
        today_advice="B changed",
        sleep_analysis_jp="C changed",
        today_condition_forecast_jp="D changed",
        diary_input_hash="diary-hash",
        today_advice_input_hash="advice-hash",
        weather_input_hash="weather-hash",
    )
    from scripts.mail_dedupe import build_mail_input_snapshot, sha256_hex, snapshot_json

    h1 = sha256_hex(snapshot_json(build_mail_input_snapshot(s1, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})))
    h2 = sha256_hex(snapshot_json(build_mail_input_snapshot(s2, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})))
    assert h1 == h2


def test_mail_input_hash_changes_when_structured_input_changes():
    base = _summary(weather_input_hash="weather-a", meal_photos=["a"], sleep_score=80.0)
    changed = _summary(weather_input_hash="weather-b", meal_photos=["b"], sleep_score=81.0)
    from scripts.mail_dedupe import build_mail_input_snapshot, sha256_hex, snapshot_json

    h1 = sha256_hex(snapshot_json(build_mail_input_snapshot(base, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})))
    h2 = sha256_hex(snapshot_json(build_mail_input_snapshot(changed, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})))
    assert h1 != h2


def test_notify_diary_phase_does_not_send_mail(monkeypatch):
    called = []
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: called.append("send"))
    monkeypatch.setattr("scripts.daily_job.render_diary_notification_mail", lambda *_args, **_kwargs: called.append("render"))
    monkeypatch.setattr("scripts.daily_job.run_phase_c", lambda *_args, **kwargs: kwargs["deps"].run_notify(SimpleNamespace(target_date="2026-04-01")))
    daily_job.run_notify_diary(_cfg(), "2026-04-01", "r1")
    assert called == []


def test_publish_uses_today_jst_for_f_risk_target_date(monkeypatch):
    summary = _summary(target_date="2026-04-01")
    captured = {}
    monkeypatch.setattr("scripts.daily_job.read_daily_log", lambda **_kwargs: summary)
    monkeypatch.setattr("scripts.daily_job.render_mail", lambda *_args, **_kwargs: SimpleNamespace(subject="S", plain_text="P", html_body="H"))
    monkeypatch.setattr("scripts.daily_job._compute_expense_f_alert", lambda **_kwargs: {"matched": True, "summary": "detected"})
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: None)
    saved=[]
    monkeypatch.setattr("scripts.daily_job._save_daily_log_fields", lambda *_args, **kwargs: saved.append(kwargs["payload"]) or {"updated": True})
    monkeypatch.setattr("scripts.daily_job._refresh_daily_log_summary", lambda *_args, **_kwargs: SimpleNamespace(mail_input_hash=saved[-1]["mail_input_hash"], mail_version=saved[-1]["mail_version"], mail_sent_at=saved[-1]["mail_sent_at"], mail_input_snapshot_json=saved[-1]["mail_input_snapshot"]))
    class MockDateTime:
        @staticmethod
        def now(_tz):
            return dt.datetime(2026, 4, 2, 0, 30, 0, tzinfo=daily_job.JST)

    monkeypatch.setattr("scripts.daily_job.datetime", MockDateTime)

    def _fake_f_risk(_config, *, summary, run_id, target_date_override=None):
        captured["target_date_override"] = target_date_override
        return {"matched": False, "alert_text": "", "reason": "ok", "score": 0.1}

    monkeypatch.setattr("scripts.daily_job._compute_f_risk_alert_runtime", _fake_f_risk)
    daily_job.run_publish(_cfg(), "2026-04-01", "r1")
    assert captured["target_date_override"] == "2026-04-02"


def test_publish_raises_when_persistence_verification_fails(monkeypatch):
    summary = _summary()
    stale = _summary(mail_input_hash=None, mail_version=None, mail_sent_at=None)
    reads = [summary, stale]
    monkeypatch.setattr("scripts.daily_job.read_daily_log", lambda **_kwargs: reads.pop(0) if reads else stale)
    monkeypatch.setattr("scripts.daily_job.render_mail", lambda *_args, **_kwargs: SimpleNamespace(subject="S", plain_text="P", html_body="H"))
    monkeypatch.setattr("scripts.daily_job._compute_expense_f_alert", lambda **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job._compute_f_risk_alert_runtime", lambda *_args, **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("scripts.daily_job._save_daily_log_fields", lambda *_args, **_kwargs: {"updated": True})
    monkeypatch.setenv("MAIL_METADATA_PERSIST_VERIFY_RETRIES", "2")
    monkeypatch.setenv("MAIL_METADATA_PERSIST_VERIFY_BACKOFF_SECONDS", "0")
    import pytest
    with pytest.raises(RuntimeError, match="attempts=2"):
        daily_job.run_publish(_cfg(), "2026-04-01", "r1")


def test_publish_passes_expense_f_alert_to_render_and_snapshot(monkeypatch):
    summary = _summary()
    reads=[summary, summary]
    monkeypatch.setattr("scripts.daily_job.read_daily_log", lambda **_kwargs: reads.pop(0) if reads else summary)
    expense_alert = {"matched": True, "summary": "F detected", "alert_text": "alert"}
    captured = {}
    monkeypatch.setattr("scripts.daily_job._compute_expense_f_alert", lambda **_kwargs: expense_alert)
    monkeypatch.setattr("scripts.daily_job._compute_f_risk_alert_runtime", lambda *_args, **_kwargs: {"matched": False, "summary": ""})
    def _render(_summary, *, expense_f_alert, f_risk_alert):
        captured["render_expense"] = expense_f_alert
        return SimpleNamespace(subject="S", plain_text="P", html_body="H")
    def _snapshot(_summary, *, expense_f_alert, f_risk_alert):
        captured["snapshot_expense"] = expense_f_alert
        return {"target_date": "2026-04-01", "expense_f": bool(expense_f_alert.get("matched"))}
    monkeypatch.setattr("scripts.daily_job.render_mail", _render)
    monkeypatch.setattr("scripts.daily_job.build_mail_input_snapshot", _snapshot)
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: None)
    saved=[]
    monkeypatch.setattr("scripts.daily_job._save_daily_log_fields", lambda *_args, **kwargs: saved.append(kwargs["payload"]) or {"updated": True})
    monkeypatch.setattr("scripts.daily_job._refresh_daily_log_summary", lambda *_args, **_kwargs: SimpleNamespace(mail_input_hash=saved[-1]["mail_input_hash"], mail_version=saved[-1]["mail_version"], mail_sent_at=saved[-1]["mail_sent_at"], mail_input_snapshot_json=saved[-1]["mail_input_snapshot"]))
    daily_job.run_publish(_cfg(), "2026-04-01", "r1")
    assert captured["render_expense"] == expense_alert
    assert captured["snapshot_expense"] == expense_alert


def test_publish_persist_verify_retries_until_second_attempt_success(monkeypatch):
    summary = _summary()
    sent = []
    saved = []
    monkeypatch.setattr("scripts.daily_job.read_daily_log", lambda **_kwargs: summary)
    monkeypatch.setattr("scripts.daily_job.render_mail", lambda *_args, **_kwargs: SimpleNamespace(subject="S", plain_text="P", html_body="H"))
    monkeypatch.setattr("scripts.daily_job._compute_expense_f_alert", lambda **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job._compute_f_risk_alert_runtime", lambda *_args, **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: sent.append(True))
    monkeypatch.setattr("scripts.daily_job._save_daily_log_fields", lambda *_args, **kwargs: saved.append(kwargs["payload"]) or {"updated": True})
    stale = SimpleNamespace(mail_input_hash="", mail_version=None, mail_sent_at="", mail_input_snapshot_json="")
    def _refresh(*_args, **_kwargs):
        if len(sent) == 1 and len(saved) == 1 and _refresh.calls == 0:
            _refresh.calls += 1
            return stale
        return SimpleNamespace(mail_input_hash=saved[-1]["mail_input_hash"], mail_version=saved[-1]["mail_version"], mail_sent_at=saved[-1]["mail_sent_at"], mail_input_snapshot_json=saved[-1]["mail_input_snapshot"])
    _refresh.calls = 0
    monkeypatch.setattr("scripts.daily_job._refresh_daily_log_summary", _refresh)
    monkeypatch.setenv("MAIL_METADATA_PERSIST_VERIFY_RETRIES", "3")
    monkeypatch.setenv("MAIL_METADATA_PERSIST_VERIFY_BACKOFF_SECONDS", "0")
    daily_job.run_publish(_cfg(), "2026-04-01", "r1")
    assert len(sent) == 1
    assert len(saved) == 1


def test_publish_persist_verify_retries_on_refresh_exception(monkeypatch):
    summary = _summary()
    sent = []
    saved = []
    monkeypatch.setattr("scripts.daily_job.read_daily_log", lambda **_kwargs: summary)
    monkeypatch.setattr("scripts.daily_job.render_mail", lambda *_args, **_kwargs: SimpleNamespace(subject="S", plain_text="P", html_body="H"))
    monkeypatch.setattr("scripts.daily_job._compute_expense_f_alert", lambda **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job._compute_f_risk_alert_runtime", lambda *_args, **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: sent.append(True))
    monkeypatch.setattr("scripts.daily_job._save_daily_log_fields", lambda *_args, **kwargs: saved.append(kwargs["payload"]) or {"updated": True})
    def _refresh(*_args, **_kwargs):
        if _refresh.calls == 0:
            _refresh.calls += 1
            raise RuntimeError("temporary read failure")
        return SimpleNamespace(mail_input_hash=saved[-1]["mail_input_hash"], mail_version=saved[-1]["mail_version"], mail_sent_at=saved[-1]["mail_sent_at"], mail_input_snapshot_json=saved[-1]["mail_input_snapshot"])
    _refresh.calls = 0
    monkeypatch.setattr("scripts.daily_job._refresh_daily_log_summary", _refresh)
    monkeypatch.setenv("MAIL_METADATA_PERSIST_VERIFY_RETRIES", "3")
    monkeypatch.setenv("MAIL_METADATA_PERSIST_VERIFY_BACKOFF_SECONDS", "0")
    daily_job.run_publish(_cfg(), "2026-04-01", "r1")
    assert len(sent) == 1


def test_publish_persist_verify_backoff_zero_skips_sleep(monkeypatch):
    summary = _summary()
    monkeypatch.setattr("scripts.daily_job.read_daily_log", lambda **_kwargs: summary)
    monkeypatch.setattr("scripts.daily_job.render_mail", lambda *_args, **_kwargs: SimpleNamespace(subject="S", plain_text="P", html_body="H"))
    monkeypatch.setattr("scripts.daily_job._compute_expense_f_alert", lambda **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job._compute_f_risk_alert_runtime", lambda *_args, **_kwargs: {"matched": False, "summary": ""})
    monkeypatch.setattr("scripts.daily_job.send_mail", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("scripts.daily_job._save_daily_log_fields", lambda *_args, **_kwargs: {"updated": True})
    monkeypatch.setattr("scripts.daily_job._refresh_daily_log_summary", lambda *_args, **_kwargs: SimpleNamespace(mail_input_hash="", mail_version=None, mail_sent_at="", mail_input_snapshot_json=""))
    sleeps = []
    monkeypatch.setattr("scripts.daily_job.time.sleep", lambda sec: sleeps.append(sec))
    monkeypatch.setenv("MAIL_METADATA_PERSIST_VERIFY_RETRIES", "2")
    monkeypatch.setenv("MAIL_METADATA_PERSIST_VERIFY_BACKOFF_SECONDS", "0")
    import pytest
    with pytest.raises(RuntimeError):
        daily_job.run_publish(_cfg(), "2026-04-01", "r1")
    assert sleeps == []
