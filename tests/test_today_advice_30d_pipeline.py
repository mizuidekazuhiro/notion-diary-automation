from __future__ import annotations

import json
import importlib.util

import pytest
from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts.note_batch_labeler import label_notes_in_batches, parse_note_label_json
from scripts.today_advice_feature_builder import build_daily_feature_table
from scripts.today_advice_pattern_analyzer import analyze_exploratory_patterns
from scripts.today_advice_regression import run_low_mood_regression
from scripts.today_advice_lightgbm import run_lightgbm_low_mood
from scripts.today_advice_renderer import build_analysis_json, render_today_advice_from_analysis

HAS_PANDAS = importlib.util.find_spec("pandas") is not None
HAS_SKLEARN = importlib.util.find_spec("sklearn") is not None


def _summary(day: int, **overrides: object) -> DailyLogSummary:
    payload = dict(
        target_date=f"2026-03-{day:02d}",
        date=None,
        target_date_value=None,
        page_id="p",
        title="t",
        summary_text="",
        summary_html="",
        mail_id="m",
        source=None,
        diary=None,
        meal_summary=None,
        meal_photos=[],
        place=None,
        activity_summary=None,
        done_count=2,
        done_tasks=[],
        done_tasks_detail=[],
        drop_count=1,
        drop_tasks=[],
        kcal=None,
        protein=None,
        fat=None,
        carb=None,
        expenses_total=1000,
        expenses=ExpenseSummary(total=1000, count=1, top=[], remaining=0),
        location_summary=None,
        mood="★★★",
        notes="ふつう",
        weight=None,
        sleep_start="2026-03-01T01:30:00+09:00",
        sleep_end="2026-03-01T07:00:00+09:00",
        sleep_duration_min=330,
        sleep_score=65,
        sleep_source=None,
        readiness_stars=None,
        readiness_hrv=None,
        readiness_bpm=None,
        baseline_hrv=None,
        baseline_waking_bpm=None,
        sleep_heart_rate=None,
        deep_duration_min=80,
        rem_duration_min=90,
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
    payload.update(overrides)
    return DailyLogSummary(**payload)


def test_note_batch_label_json_parse() -> None:
    rows = [{"date": "2026-03-01", "notes": "疲れた"}]
    raw = json.dumps([
        {"date": "2026-03-01", "sentiment_label": "negative", "sentiment_score": -2, "fatigue_flag": True}
    ], ensure_ascii=False)
    parsed = parse_note_label_json(raw, rows)
    assert parsed[0].sentiment_label in {"negative", "unknown"}
    assert parsed[0].fatigue_flag is True


def test_note_empty_fallback_neutral() -> None:
    items = [_summary(1, notes="")]
    result = label_notes_in_batches(summaries=items, model="m", chat_completion=lambda **kwargs: "[]")
    assert result["2026-03-01"].sentiment_label in {"neutral", "unknown"}
    assert result["2026-03-01"].confidence == "low"


def test_lag_pattern_decision_thresholds() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    histories = [_summary(i, mood="★" if i % 2 == 0 else "★★★★", notes="疲れ") for i in range(1, 12)]
    labels = {h.target_date: parse_note_label_json('[{"date":"%s","fatigue_flag":true,"sentiment_label":"negative","sentiment_score":-1}]' % h.target_date, [{"date": h.target_date, "notes": h.notes}])[0] for h in histories}
    df = build_daily_feature_table(histories, labels)
    result = analyze_exploratory_patterns(df)
    assert len(result["univariate_summary"]) >= 8
    assert "top_combination_patterns_for_low_mood" in result


def test_logistic_regression_minimum_run() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    histories = [_summary(i, mood="★" if i % 3 == 0 else "★★★★", notes="疲れ" if i % 2 == 0 else "回復") for i in range(1, 16)]
    labels = {}
    for h in histories:
        flag = "true" if "疲れ" in (h.notes or "") else "false"
        labels[h.target_date] = parse_note_label_json(f'[{{"date":"{h.target_date}","fatigue_flag":{flag},"sentiment_label":"negative","sentiment_score":-1}}]', [{"date": h.target_date, "notes": h.notes}])[0]
    df = build_daily_feature_table(histories, labels)
    result = run_low_mood_regression(df)
    assert "available" in result
    assert "sample_size" in result
    if HAS_SKLEARN:
        assert isinstance(result["available"], bool)


def test_analysis_json_builder() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    histories = [_summary(i) for i in range(1, 10)]
    labels = {h.target_date: parse_note_label_json('[{"date":"%s"}]' % h.target_date, [{"date": h.target_date, "notes": h.notes}])[0] for h in histories}
    df = build_daily_feature_table(histories, labels)
    payload = build_analysis_json(
        target_date="2026-03-10",
        today_summary=_summary(10),
        features_df=df,
        exploratory_summary={"matched_today_conditions": [], "top_single_features_for_low_mood": []},
        regression_summary={"available": False, "sample_size": 0},
        lightgbm_summary={"available": False, "sample_size": 0},
    )
    assert payload["target_date"] == "2026-03-10"
    assert "today_sleep_context" in payload
    assert "recent_7d_summary" in payload


def test_gpt_failure_fallback_message() -> None:
    text = render_today_advice_from_analysis(
        analysis_json={"today_sleep_context": {"sleep_hours": 5.5}, "primary_focus": "回復優先"},
        model="x",
        chat_completion=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert "回復優先" in text
    assert "午前中の重い判断" in text


def test_no_pattern_no_evidence_returns_unknown_pattern_message() -> None:
    text = render_today_advice_from_analysis(
        analysis_json={"matched_patterns_count": 0, "exploratory_summary": {"top_single_features_for_low_mood": []}, "today_sleep_context": {"sleep_available": False, "sleep_hours": 6.2}},
        model="x",
        chat_completion=lambda **kwargs: "一般論です。",
    )
    assert "限定的" in text


def test_analysis_audit_json_has_required_keys(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator

    histories = [_summary(i, mood="★" if i % 2 == 0 else "★★★★", notes="疲れ" if i % 3 == 0 else "回復") for i in range(1, 14)]
    target = _summary(20, target_date="2026-03-20", sleep_duration_min=290, sleep_score=62)
    monkeypatch.setenv("TODAY_ADVICE_DEBUG", "true")
    monkeypatch.setattr(generator, "load_daily_logs_for_period", lambda **kwargs: [target, *histories])
    monkeypatch.setattr(
        generator,
        "_chat_completion",
        lambda **kwargs: "監査ログ確認用の本文です。",
    )

    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    assert result is not None
    audit = result.judgment_json["analysis_audit"]
    for key in [
        "target_date",
        "fetch",
        "notes_labeling",
        "features",
        "exploratory_analysis",
        "regression",
        "lightgbm",
        "today_match",
        "analysis_json",
        "final_text",
        "notes_fallback_reason_counts",
        "sleep_feature_conversion_samples",
        "matched_patterns_count",
        "evidence_used",
    ]:
        assert key in audit


def test_exploratory_analysis_is_included_in_audit_log(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator

    histories = [_summary(i, mood="★" if i % 2 == 0 else "★★★★", notes="疲れ") for i in range(1, 13)]
    target = _summary(20, target_date="2026-03-20")
    monkeypatch.setenv("TODAY_ADVICE_DEBUG", "true")
    monkeypatch.setattr(generator, "load_daily_logs_for_period", lambda **kwargs: [target, *histories])
    monkeypatch.setattr(generator, "_chat_completion", lambda **kwargs: "本文")

    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    assert result is not None
    exploratory = result.judgment_json["analysis_audit"]["exploratory_analysis"]
    assert exploratory["exploratory_feature_count"] > 0
    assert isinstance(exploratory["top_single_features_for_low_mood"], list)


def test_notes_label_count_reflected_in_audit_log(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator

    histories = [_summary(i, notes="" if i % 2 == 0 else "疲れ") for i in range(1, 11)]
    target = _summary(20, target_date="2026-03-20")
    monkeypatch.setenv("TODAY_ADVICE_DEBUG", "true")
    monkeypatch.setattr(generator, "load_daily_logs_for_period", lambda **kwargs: [target, *histories])
    monkeypatch.setattr(generator, "_chat_completion", lambda **kwargs: "本文")

    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    assert result is not None
    notes = result.judgment_json["analysis_audit"]["notes_labeling"]
    assert notes["total_count"] == len(histories)
    assert notes["non_empty_count"] == len([h for h in histories if (h.notes or "").strip()])


def test_regression_availability_reflected_in_audit_log(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator

    histories = [_summary(i, mood="★" if i % 3 == 0 else "★★★★", notes="疲れ" if i % 2 == 0 else "回復") for i in range(1, 19)]
    target = _summary(20, target_date="2026-03-20")
    monkeypatch.setenv("TODAY_ADVICE_DEBUG", "true")
    monkeypatch.setattr(generator, "load_daily_logs_for_period", lambda **kwargs: [target, *histories])
    monkeypatch.setattr(generator, "_chat_completion", lambda **kwargs: "本文")

    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    assert result is not None
    regression = result.judgment_json["analysis_audit"]["regression"]
    assert "available" in regression
    assert "sample_size" in regression


def test_final_text_is_saved_in_audit_log(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator

    histories = [_summary(i, notes="疲れ") for i in range(1, 12)]
    target = _summary(20, target_date="2026-03-20")
    monkeypatch.setenv("TODAY_ADVICE_DEBUG", "true")
    monkeypatch.setattr(generator, "load_daily_logs_for_period", lambda **kwargs: [target, *histories])
    monkeypatch.setattr(generator, "_chat_completion", lambda **kwargs: "これは最終本文です。")

    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    assert result is not None
    assert result.judgment_json["analysis_audit"]["final_text"]["text"] == "これは最終本文です。"


def test_sleep_duration_zero_marked_invalid() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    h = _summary(1, sleep_duration_min=0)
    df = build_daily_feature_table([h], {})
    assert bool(df.iloc[0]["sleep_valid_flag"]) is False
    assert df.iloc[0]["sleep_invalid_reason"] == "zero_duration"


def test_invalid_sleep_not_treated_as_short_sleep() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    h = _summary(1, sleep_duration_min=0)
    df = build_daily_feature_table([h], {})
    assert bool(df.iloc[0]["sleep_lt_6h_flag"]) is False


def test_invalid_sleep_excluded_from_sleep_lag_features() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    hs = [_summary(1, sleep_duration_min=0), _summary(2, sleep_duration_min=420)]
    df = build_daily_feature_table(hs, {})
    assert str(df.iloc[0]["sleep_hours"]) == "nan"
    assert df.iloc[0]["sleep_vs_7d_delta"] != df.iloc[0]["sleep_vs_7d_delta"]


def test_invalid_sleep_day_keeps_non_sleep_features() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    h = _summary(1, sleep_duration_min=0, notes="疲れ", kcal=2000, protein=100, done_count=3)
    df = build_daily_feature_table([h], {})
    assert bool(df.iloc[0]["notes_present_flag"]) is True
    assert df.iloc[0]["protein"] == 100
    assert df.iloc[0]["task_done_count"] == 3


def test_analysis_json_contains_exploratory_regression_lightgbm() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    hs = [_summary(i) for i in range(1, 12)]
    df = build_daily_feature_table(hs, {})
    payload = build_analysis_json(
        target_date="2026-03-12",
        today_summary=_summary(12),
        features_df=df,
        exploratory_summary=analyze_exploratory_patterns(df),
        regression_summary=run_low_mood_regression(df),
        lightgbm_summary=run_lightgbm_low_mood(df),
    )
    assert "exploratory_summary" in payload
    assert "regression_summary" in payload
    assert "lightgbm_summary" in payload


def test_today_sleep_invalid_sets_sleep_available_false() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    hs = [_summary(i, sleep_duration_min=420) for i in range(1, 8)] + [_summary(8, sleep_duration_min=0)]
    df = build_daily_feature_table(hs, {})
    payload = build_analysis_json(
        target_date="2026-03-08",
        today_summary=_summary(8, sleep_duration_min=0),
        features_df=df,
        exploratory_summary=analyze_exploratory_patterns(df),
        regression_summary={"available": False, "sample_size": 0},
        lightgbm_summary={"available": False, "sample_size": 0},
    )
    assert bool(payload["today_sleep_context"]["sleep_available"]) is False


def test_today_advice_prompt_prefers_exploratory_evidence() -> None:
    analysis = {
        "matched_patterns_count": 1,
        "exploratory_summary": {"top_single_features_for_low_mood": [{"feature": "notes_stress_flag"}]},
        "today_sleep_context": {"sleep_available": False, "sleep_hours": None},
    }
    got = render_today_advice_from_analysis(
        analysis_json=analysis,
        model="x",
        chat_completion=lambda **kwargs: kwargs["user_prompt"],
    )
    assert "analysis=" in got


def test_leakage_columns_excluded_from_rankings() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    hs = [_summary(i, mood="★" if i % 2 == 0 else "★★★★") for i in range(1, 16)]
    df = build_daily_feature_table(hs, {})
    result = analyze_exploratory_patterns(df)
    ranked = [x["feature"] for x in result["top_single_features_for_low_mood"]]
    assert "next_day_low_mood_flag" not in ranked
    assert "next_day_mood_score" not in ranked


def test_lightgbm_failure_has_specific_reason() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    hs = [_summary(i, mood="★★★★") for i in range(1, 8)]
    df = build_daily_feature_table(hs, {})
    result = run_lightgbm_low_mood(df)
    assert "skipped_reason" in result
    assert result["skipped_reason"] in {
        "lightgbm_not_installed",
        "insufficient_samples",
        "single_class_target",
        "too_many_missing_values",
        "unsupported_dtype",
        "fit_exception",
    }


def test_fallback_text_does_not_add_new_causality() -> None:
    text = render_today_advice_from_analysis(
        analysis_json={
            "today_sleep_context": {"sleep_available": False, "sleep_hours": None},
            "matched_patterns_count": 0,
            "exploratory_summary": {"top_single_features_for_low_mood": []},
        },
        model="x",
        chat_completion=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert "睡眠データ" in text and "不明" in text
    assert "限定的" in text or "過去傾向" in text
