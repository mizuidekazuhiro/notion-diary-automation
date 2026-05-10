from publish.email_templates import render_daily_log_text
from scripts.mail_dedupe import build_mail_input_snapshot, snapshot_json, sha256_hex
from types import SimpleNamespace


def _payload(**kwargs):
    base = {
        "target_date": "2026-05-10",
        "run_id": "r1",
        "study_minutes": None,
        "study_sessions": None,
        "study_last_used_at": "",
    }
    base.update(kwargs)
    return base


def test_study_section_shows_when_study_minutes_zero():
    text = render_daily_log_text(_payload(study_minutes=0, study_sessions=0))
    assert "司法試験 Study" in text
    assert "0時間（0分）" in text


def test_study_section_shows_when_only_sessions_or_last_used():
    text_sessions = render_daily_log_text(_payload(study_sessions=2))
    assert "司法試験 Study" in text_sessions
    text_last = render_daily_log_text(_payload(study_last_used_at="2026-05-10T09:00:00+09:00"))
    assert "司法試験 Study" in text_last


def test_study_section_hidden_when_all_unrecorded():
    text = render_daily_log_text(_payload())
    assert "司法試験 Study" not in text


def test_mail_input_hash_changes_when_study_values_change():
    base = SimpleNamespace(study_minutes=None, study_sessions=None, study_last_used_at=None, expenses=SimpleNamespace(count=0, top=[]))
    snap1 = build_mail_input_snapshot(base, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})
    base.study_minutes = 0
    snap2 = build_mail_input_snapshot(base, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})
    base.study_minutes = 90
    snap3 = build_mail_input_snapshot(base, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})
    assert sha256_hex(snapshot_json(snap1)) != sha256_hex(snapshot_json(snap2))
    assert sha256_hex(snapshot_json(snap2)) != sha256_hex(snapshot_json(snap3))
