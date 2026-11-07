from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from scripts.today_advice_pattern_analyzer import analyze_exploratory_patterns
from scripts.today_advice_regression import run_low_mood_regression
from scripts.today_advice_lightgbm import run_lightgbm_low_mood
from tests.test_today_advice_30d_pipeline import _summary


def frame(moods, **features):
    return pd.DataFrame({
        "date": pd.date_range("2026-10-01", periods=len(moods)).strftime("%Y-%m-%d"),
        "mood": moods, "notes_fatigue_flag": False,
        "task_done_count": 2, "task_drop_count": 0, **features,
    })


def test_all_missing_outcomes_are_not_negative_or_risk():
    df = frame([None] * 29, sentiment_unknown=True, no_signal_note=True, tag_extract_failed=True)
    result = analyze_exploratory_patterns(df)
    assert result["top_combination_patterns_for_low_mood"] == []
    assert result["matched_today_conditions"] == []
    assert result["target_counts"] == {"valid": 0, "missing": 29, "positive": 0, "negative": 0}
    for model in (run_low_mood_regression, run_lightgbm_low_mood):
        fit = model(df)
        assert fit["sample_size"] == 0
        assert fit["available"] is False


def test_equal_rates_are_not_risk_and_quality_flags_never_rank():
    df = frame([1, 5] * 12, a=[True] * 12 + [False] * 12,
               b=True, sentiment_unknown=[True, False] * 12)
    result = analyze_exploratory_patterns(df)
    assert result["top_combination_patterns_for_low_mood"] == []
    ranked = {x["feature"] for x in result["top_single_features_for_low_mood"]}
    assert "sentiment_unknown" not in ranked


def test_insufficient_class_support_abstains():
    result = analyze_exploratory_patterns(frame([1] + [5] * 20, a=True, b=True))
    assert result["top_combination_patterns_for_low_mood"] == []
    assert "insufficient_class_support" in result["reason_codes"]


def test_same_support_is_one_association_and_needs_explicit_target_row():
    df = frame([1] * 6 + [5] * 6, a=[True] * 6 + [False] * 6,
               b=True, c=[True] * 6 + [False] * 6)
    result = analyze_exploratory_patterns(df)
    assert len(result["top_combination_patterns_for_low_mood"]) == 1
    assert result["matched_today_conditions"] == []
    matched = analyze_exploratory_patterns(df, today_features={"a": True, "b": True, "c": True},
                                           today_feature_date="2026-10-13")
    assert len(matched["matched_today_conditions"]) == 1
    assert matched["match_source_date"] == "2026-10-13"


def test_training_does_not_create_today_from_latest_historical_row():
    df = frame([1, 5] * 8, sleep_hours=6.0, sleep_valid_flag=True)
    result = run_lightgbm_low_mood(df)
    assert result["prediction_probability_for_today"] is None
    assert result["feature_row_date_used_as_today"] is None
    assert result["today_contribution_features"] == []


def test_missing_target_page_is_not_replaced_by_a_past_page(monkeypatch):
    from scripts import mood_advice_generator as generator
    old = _summary(4, target_date="2026-11-04")
    monkeypatch.setattr(generator, "_load_daily_logs_for_period_with_debug", lambda **kw:
        generator.TodayAdviceHistoryLoadResult([old], ["2026-11-05", "2026-11-04"], [], ["2026-11-05"], False))
    assert generator.build_today_advice_generation_context(
        daily_log_read_url="read", bearer_token=None, target_date="2026-11-05") is None


def test_unknown_features_remain_unknown_while_explicit_zero_survives():
    from scripts.today_advice_feature_builder import build_daily_feature_table
    unknown = _summary(1, notes=None, location_summary=None, done_count=None, drop_count=None, expenses_total=None)
    zero = replace(unknown, target_date="2026-03-02", done_count=0, drop_count=0, expenses_total=0)
    df = build_daily_feature_table([unknown, zero], {})
    assert pd.isna(df.iloc[0]["task_done_count"])
    assert pd.isna(df.iloc[0]["notes_fatigue_flag"])
    assert pd.isna(df.iloc[0]["late_outing_flag"])
    assert df.iloc[1]["task_done_count"] == 0
    assert df.iloc[1]["spending_total"] == 0


def test_renderer_never_counts_matches_as_validated_high_risk():
    from scripts.today_advice_renderer import build_analysis_json
    df = frame([None] * 29, sleep_valid_flag=False, sleep_invalid_reason="missing", notes_present_flag=False)
    result = build_analysis_json(target_date="2026-11-05", today_summary=_summary(1), features_df=df,
        exploratory_summary={"matched_today_conditions": [{"delta": 0}] * 3},
        regression_summary={"available": False}, lightgbm_summary={"available": False})
    assert result["risk_level"] == "unknown"
    assert result["primary_focus"] != "回復優先"


def test_render_result_records_short_insufficiency_and_failure():
    from scripts.today_advice_renderer import render_today_advice_result
    no_data = render_today_advice_result(analysis_json={"risk_level": "unknown", "target_counts": {"valid": 0}},
        model="test", chat_completion=lambda **kw: pytest.fail("insufficient data must not call AI"))
    assert no_data.status == "insufficient_data"
    assert len(no_data.text) < 120
    assert not no_data.fallback_used
    failed = render_today_advice_result(analysis_json={"target_counts": {"valid": 12}, "evidence_used": ["related"]},
        model="test", chat_completion=lambda **kw: (_ for _ in ()).throw(TimeoutError()))
    assert failed.fallback_used
    assert failed.status == "degraded"
    assert failed.rejection_reason == "TimeoutError"
    assert "20〜30分" not in failed.text


def test_duplicate_dates_do_not_multiply_support():
    df = frame([1, 5], a=[True, False], b=True)
    result = analyze_exploratory_patterns(pd.concat([df] * 6, ignore_index=True))
    assert result["target_counts"]["valid"] == 2
    assert result["top_combination_patterns_for_low_mood"] == []


def test_missing_features_do_not_reappear_as_zero_streaks_or_interactions():
    from scripts.today_advice_feature_builder import build_daily_feature_table
    histories = [_summary(i, mood="★★★★★" if i < 13 else "★", notes=None, study_minutes=None,
                          sleep_start=None, sleep_end=None, sleep_duration_min=None, done_count=None, drop_count=3)
                 for i in range(1, 25)]
    df = build_daily_feature_table(histories, {})
    for col in ("study_zero_day_streak", "notes_has_fatigue", "late_work_flag", "sleep_short_flag",
                "task_completion_ratio", "task_balance_bad_flag", "stress_x_late_work"):
        assert df[col].isna().all(), col
    ranked = {x["feature"] for x in analyze_exploratory_patterns(df)["top_single_features_for_low_mood"]}
    assert "study_zero_day_streak" not in ranked


def test_lag_means_previous_calendar_day_and_never_copies_gap():
    from scripts.today_advice_feature_builder import build_daily_feature_table
    df = build_daily_feature_table([_summary(1, study_minutes=20), _summary(5, study_minutes=30)], {})
    assert pd.isna(df.loc[df["date"] == "2026-03-05", "study_minutes_lag_1"].iloc[0])
    assert pd.isna(df.loc[df["date"] == "2026-03-03", "study_minutes"].iloc[0])


def test_zero_importance_never_becomes_lightgbm_risk():
    result = run_lightgbm_low_mood(frame([1, 5] * 12, constant=0))
    assert result["top_risk_features"] == []
