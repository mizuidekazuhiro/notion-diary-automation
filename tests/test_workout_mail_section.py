from types import SimpleNamespace

from publish.email_templates import render_daily_log_html, render_daily_log_text
from scripts.mail_dedupe import build_mail_input_snapshot, sha256_hex, snapshot_json


def _payload(**kwargs):
    base = {
        "target_date": "2026-09-20",
        "run_id": "r1",
        "workout_done": False,
        "workout_sessions": 0,
        "workout_gym": "",
        "workout_duration_min": 0,
        "workout_sets": 0,
        "workout_volume_kg": 0,
        "workout_calories": 0,
        "workout_exercises": "",
        "workout_summary": "トレーニング記録なし",
    }
    base.update(kwargs)
    return base


def test_workout_section_shows_in_text_and_html_when_done():
    payload = _payload(
        workout_done=True,
        workout_sessions=1,
        workout_gym="インフィニティ24",
        workout_duration_min=62,
        workout_sets=18,
        workout_volume_kg=6420,
        workout_calories=410,
        workout_exercises="ベンチプレス、ラットプルダウン",
        workout_summary="1回 / 62分 / 18セット",
    )
    text = render_daily_log_text(payload)
    html = render_daily_log_html(payload)
    assert "🏋️ Workout" in text
    assert "インフィニティ24" in text
    assert "6,420 kg" in text
    assert "🏋️ Workout" in html
    assert "ベンチプレス" in html


def test_workout_section_hidden_when_no_workout():
    text = render_daily_log_text(_payload())
    html = render_daily_log_html(_payload())
    assert "🏋️ Workout" not in text
    assert "🏋️ Workout" not in html


def test_mail_input_hash_changes_when_workout_changes():
    base = SimpleNamespace(
        workout_done=False,
        workout_sessions=0,
        workout_gym=None,
        workout_duration_min=0,
        workout_sets=0,
        workout_volume_kg=0,
        workout_calories=0,
        workout_exercises=None,
        workout_summary="トレーニング記録なし",
        expenses=SimpleNamespace(count=0, top=[]),
        done_tasks_detail=[],
    )
    snap1 = build_mail_input_snapshot(base, expense_f_alert={}, f_risk_alert={})
    base.workout_done = True
    base.workout_gym = "インフィニティ24"
    base.workout_sets = 18
    base.workout_volume_kg = 6420
    base.workout_summary = "1回 / 62分 / 18セット"
    snap2 = build_mail_input_snapshot(base, expense_f_alert={}, f_risk_alert={})
    assert sha256_hex(snapshot_json(snap1)) != sha256_hex(snapshot_json(snap2))
