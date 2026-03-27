from publish.email_templates import render_daily_log_html, render_daily_log_text
from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts.sleep_condition_generator import build_sleep_insight_context


def _summary(**overrides):
    base = dict(
        target_date="2026-03-20",
        date="2026-03-20",
        target_date_value="2026-03-20",
        page_id="page",
        title="Daily Log｜2026-03-20",
        summary_text="",
        summary_html="",
        mail_id="run",
        source="automation",
        diary="",
        meal_summary=None,
        meal_photos=[],
        place=None,
        activity_summary=None,
        done_count=0,
        done_tasks=[],
        done_tasks_detail=[],
        drop_count=0,
        drop_tasks=[],
        kcal=None,
        protein=None,
        fat=None,
        carb=None,
        expenses_total=None,
        expenses=ExpenseSummary(total=0, count=0, top=[], remaining=0),
        location_summary=None,
        mood=None,
        notes=None,
        weight=None,
        sleep_start=None,
        sleep_end=None,
        sleep_duration_min=None,
        resolved_sleep_duration_min=None,
        resolved_sleep_duration_hours=None,
        resolved_sleep_duration_text=None,
        sleep_duration_source="missing",
        sleep_score=None,
        sleep_source=None,
        readiness_stars=None,
        readiness_hrv=None,
        readiness_bpm=None,
        baseline_hrv=None,
        baseline_waking_bpm=None,
        sleep_heart_rate=None,
        deep_duration_min=None,
        rem_duration_min=None,
        sleep_analysis_jp=None,
        today_condition_forecast_jp=None,
        today_advice=None,
        diary_input_hash=None,
        today_advice_input_hash=None,
        diary_generated_at=None,
        today_advice_generated_at=None,
        page_url=None,
        diary_notification_sent=None,
    )
    base.update(overrides)
    return DailyLogSummary(**base)


def test_build_sleep_insight_context_partial_history():
    today = _summary(
        sleep_duration_min=420,
        resolved_sleep_duration_min=420,
        resolved_sleep_duration_hours=7.0,
        resolved_sleep_duration_text="7時間0分",
        sleep_duration_source="derived_from_start_end",
        sleep_score=78,
        readiness_hrv=55,
        readiness_bpm=52,
        baseline_waking_bpm=49,
        deep_duration_min=90,
        rem_duration_min=110,
    )
    history = [
        _summary(target_date="2026-03-19", sleep_duration_min=400, sleep_score=80, readiness_hrv=50, readiness_bpm=51),
        _summary(target_date="2026-03-18", sleep_duration_min=380, sleep_score=None, readiness_hrv=60, readiness_bpm=53),
    ]

    context = build_sleep_insight_context(today_summary=today, history_summaries=history)

    assert context.trend_values["sleep_duration_min_7d_avg"] == 390
    assert context.trend_values["sleep_duration_min_delta_vs_7d"] == 30
    assert context.trend_values["readiness_hrv_7d_avg"] == 55
    assert context.trend_values["readiness_bpm_delta_vs_7d"] == 0
    assert context.today_values["baseline_waking_bpm"] == 49
    assert "vs_yesterday" in context.trend_values
    assert "recent_3day_trend" in context.trend_values
    assert context.trend_values["sleep_score_7d_avg"] == 80
    assert context.trend_values["rem_duration_min_7d_avg"] is None


def test_render_mail_sleep_section_only_when_values_exist():
    payload = {
        "target_date": "2026-03-20",
        "run_id": "run",
        "summary_text": "",
        "diary": "",
        "meal_summary": None,
        "meal_photos": [],
        "expenses_total": None,
        "expenses": {"total": 0, "count": 0, "top": [], "remaining": 0},
        "location_summary": None,
        "mood": None,
        "weight": None,
        "sleep_analysis_jp": "昨夜は睡眠時間をしっかり確保できました。",
        "today_condition_forecast_jp": "午前は比較的安定して動けそうです。",
        "sleep_start": "2026-03-19T22:53:00+09:00",
        "sleep_end": "2026-03-20T08:33:00+09:00",
        "sleep_duration_min": 585,
        "mood_notes_url": "",
    }
    html = render_daily_log_html(payload)
    text = render_daily_log_text(payload)

    assert "Sleep &amp; Condition" in html
    assert "22:53" in html and "08:33" in html and "9時間45分" in html
    assert "Sleep & Condition" in text
    assert "- 睡眠時間: 9時間45分" in text
