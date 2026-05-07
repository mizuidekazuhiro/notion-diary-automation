from __future__ import annotations

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts.daily_job import _build_diary_hash_payload, _build_input_hash, build_diary_input_fields


def _summary() -> DailyLogSummary:
    return DailyLogSummary(
        target_date="2026-05-01",date="2026-05-01",target_date_value="2026-05-01",page_id="p",title="t",summary_text="",summary_html="",mail_id="m",source="automation",diary=None,meal_summary=None,meal_photos=[],place=None,activity_summary=None,done_count=0,done_tasks=[],done_tasks_detail=[],drop_count=0,drop_tasks=[],kcal=None,protein=None,fat=None,carb=None,expenses_total=0,expenses=ExpenseSummary(total=0,count=0,top=[],remaining=0),location_summary=None,mood=None,notes="x",weight=None,sleep_start=None,sleep_end=None,sleep_duration_min=None,resolved_sleep_duration_min=None,resolved_sleep_duration_hours=None,resolved_sleep_duration_text=None,sleep_duration_source="none",sleep_score=None,sleep_source=None,readiness_stars=None,readiness_hrv=None,readiness_bpm=None,baseline_hrv=None,baseline_waking_bpm=None,sleep_heart_rate=None,deep_duration_min=None,rem_duration_min=None,sleep_analysis_jp=None,today_condition_forecast_jp=None,today_advice=None
    )


def test_voice_notes_changes_diary_input_hash() -> None:
    summary = _summary()
    used1, _, _, _ = build_diary_input_fields(summary, voice_diary_notes_text="[09:10] a")
    payload1, _ = _build_diary_hash_payload(summary, used1)
    hash1, _, _ = _build_input_hash(payload1)

    used2, _, _, _ = build_diary_input_fields(summary, voice_diary_notes_text="[09:10] b")
    payload2, _ = _build_diary_hash_payload(summary, used2)
    hash2, _, _ = _build_input_hash(payload2)

    assert hash1 != hash2
    assert "Voice Diary Notes" in used1
