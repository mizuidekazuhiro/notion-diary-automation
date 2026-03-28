from __future__ import annotations

import importlib.util

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts import f_risk_generator
from scripts.note_batch_labeler import NoteLabel
from scripts.today_advice_feature_builder import build_daily_feature_table
from scripts.today_advice_pattern_analyzer import analyze_exploratory_patterns
from scripts.today_advice_regression import run_low_mood_regression

HAS_PANDAS = importlib.util.find_spec("pandas") is not None


def _summary(day: int, **overrides: object) -> DailyLogSummary:
    payload = dict(
        target_date=f"2026-03-{day:02d}",
        date=f"2026-03-{day:02d}",
        target_date_value=f"2026-03-{day:02d}",
        page_id="p",
        title="Daily Log",
        summary_text="",
        summary_html="",
        mail_id="run",
        source="automation",
        diary="gpt text",
        meal_summary="meal",
        meal_photos=[],
        place="home",
        activity_summary="",
        done_count=2,
        done_tasks=[],
        done_tasks_detail=[],
        drop_count=1,
        drop_tasks=[],
        kcal=1900,
        protein=80,
        fat=55,
        carb=240,
        expenses_total=1200,
        expenses=ExpenseSummary(total=1200, count=1, top=[], remaining=0),
        location_summary="home_heavy_day",
        mood="★★★",
        notes="少し疲れたがジムに行った",
        weight=None,
        sleep_start="2026-03-01T00:30:00+09:00",
        sleep_end="2026-03-01T06:30:00+09:00",
        sleep_duration_min=360,
        resolved_sleep_duration_min=360,
        resolved_sleep_duration_hours=6.0,
        resolved_sleep_duration_text="6時間00分",
        sleep_duration_source="derived_from_start_end",
        sleep_score=70,
        sleep_source="AutoSleep",
        readiness_stars=None,
        readiness_hrv=None,
        readiness_bpm=None,
        baseline_hrv=None,
        baseline_waking_bpm=None,
        sleep_heart_rate=None,
        deep_duration_min=None,
        rem_duration_min=None,
        sleep_analysis_jp="gpt sleep",
        today_condition_forecast_jp="gpt forecast",
        today_advice="gpt advice",
        diary_input_hash=None,
        today_advice_input_hash=None,
        diary_generated_at=None,
        today_advice_generated_at=None,
        page_url="https://example.com",
        diary_notification_sent=None,
    )
    payload.update(overrides)
    return DailyLogSummary(**payload)


def _label(date: str) -> NoteLabel:
    return NoteLabel(
        date=date,
        sentiment_label="negative",
        sentiment_score=-1,
        fatigue_flag=True,
        stress_flag=True,
        social_load_flag=False,
        achievement_flag=False,
        self_care_flag=True,
        sleep_issue_flag=True,
        confidence="high",
        evidence_keywords=["疲れ"],
        signals=[{"tag": "exercise", "category": "behavior", "polarity": "positive", "intensity": "medium", "confidence": 0.9, "evidence_text": "ジム"}],
        derived_flags={"exercise": True, "recovery_like_flag": True},
        parse_quality="high",
        no_signal_note=False,
        tag_extract_failed=False,
        parse_low_confidence=False,
    )


def test_daily_feature_store_adds_lag_rolling_streak_interactions() -> None:
    if not HAS_PANDAS:
        return
    histories = [_summary(i) for i in range(1, 20)]
    labels = {h.target_date: _label(h.target_date) for h in histories}
    df = build_daily_feature_table(histories, labels)
    for col in [
        "sleep_hours_lag_1",
        "spending_total_rolling_sum_7d",
        "social_load_streak",
        "sleep_short_x_social_load",
        "fatigue_x_spending_spike",
    ]:
        assert col in df.columns
    assert bool(df["forbidden_inputs_used"].eq(False).all())


def test_today_advice_targets_are_same_day() -> None:
    if not HAS_PANDAS:
        return
    histories = [_summary(i, mood="★★" if i % 4 == 0 else "★★★★") for i in range(1, 24)]
    labels = {h.target_date: _label(h.target_date) for h in histories}
    df = build_daily_feature_table(histories, labels)
    explore = analyze_exploratory_patterns(df)
    reg = run_low_mood_regression(df)
    assert explore["exploratory_target_name"] == "today_low_mood_flag"
    assert reg["regression_target_name"] == "today_low_mood_flag"


def test_f_risk_renderer_skips_when_not_matched() -> None:
    text, fallback, reason = f_risk_generator._render_f_risk_alert(
        risk_json={"risk_matched": False, "skipped_reason": None, "explanation_points": []},
        model="x",
    )
    assert text is None
    assert fallback is False
    assert reason == "not_matched"


def test_f_risk_alert_contains_reason_when_matched(monkeypatch) -> None:
    def _fake_chat_completion(**kwargs):
        return "直近3日で短睡眠とストレスが重なり、過去F日に中程度で一致しています。特に短睡眠連続とストレス×遅い稼働の一致が大きく、今日は衝動支出に注意です。"

    monkeypatch.setattr(f_risk_generator, "chat_completion", _fake_chat_completion)
    text, fallback, reason = f_risk_generator._render_f_risk_alert(
        risk_json={
            "risk_matched": True,
            "matched_patterns": ["睡眠短縮が連続", "ストレス×遅い稼働の重なり"],
            "explanation_points": ["直近数日の並びが過去F日と中程度一致", "一致した主要因: 睡眠短縮が連続, ストレス×遅い稼働の重なり"],
            "skipped_reason": None,
        },
        model="x",
    )
    assert fallback is False
    assert reason is None
    assert "一致" in (text or "")
