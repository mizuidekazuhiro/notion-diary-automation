from __future__ import annotations

import json
import importlib.util
import warnings

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
        resolved_sleep_duration_min=330,
        resolved_sleep_duration_hours=5.5,
        resolved_sleep_duration_text="5時間30分",
        sleep_duration_source="derived_from_start_end",
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
    if "resolved_sleep_duration_min" not in overrides:
        payload["resolved_sleep_duration_min"] = payload.get("sleep_duration_min")
    if "resolved_sleep_duration_hours" not in overrides:
        minutes = payload.get("resolved_sleep_duration_min")
        payload["resolved_sleep_duration_hours"] = (round(float(minutes) / 60.0, 2) if isinstance(minutes, (int, float)) else None)
    if "resolved_sleep_duration_text" not in overrides:
        payload["resolved_sleep_duration_text"] = None
    if "sleep_duration_source" not in overrides:
        payload["sleep_duration_source"] = "derived_from_start_end"
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


def test_analysis_json_weakens_notes_assertion_when_label_quality_low() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    histories = [_summary(i) for i in range(1, 10)]
    labels = {h.target_date: parse_note_label_json('[{"date":"%s","tags":["fatigue"]}]' % h.target_date, [{"date": h.target_date, "notes": h.notes}])[0] for h in histories}
    df = build_daily_feature_table(histories, labels)
    payload = build_analysis_json(
        target_date="2026-03-10",
        today_summary=_summary(10),
        features_df=df,
        exploratory_summary={"matched_today_conditions": [], "top_single_features_for_low_mood": []},
        regression_summary={"available": False, "sample_size": 0},
        lightgbm_summary={"available": False, "sample_size": 0},
        notes_label_quality={"notes_parse_success_rate": 0.4, "label_quality_low": True},
    )
    assert "疲労系Notes" in payload["recent_7d_summary"]["behavior_trend"][1]
    assert any("断定を抑制" in x for x in payload["evidence_used"])


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


def test_notes_quality_warning_when_non_empty_but_no_signals(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator
    from scripts.note_batch_labeler import neutral_label

    histories = [_summary(i, notes="疲れ") for i in range(1, 8)]
    target = _summary(20, target_date="2026-03-20")
    monkeypatch.setenv("TODAY_ADVICE_DEBUG", "true")
    monkeypatch.setattr(generator, "load_daily_logs_for_period", lambda **kwargs: [target, *histories])
    monkeypatch.setattr(generator, "_chat_completion", lambda **kwargs: "本文")

    def _fake_labeler(**kwargs: object) -> dict[str, object]:
        audit = kwargs.get("audit")
        if isinstance(audit, dict):
            audit.update(
                {
                    "api_calls": 1,
                    "notes_classifier_success_rate": 0.0,
                    "notes_parse_success_rate": 0.0,
                    "unknown_rate": 1.0,
                    "signals_detected_count": 0,
                    "top_tags": [],
                    "raw_response_paths": [],
                    "raw_sentiment_counts": {},
                    "normalized_sentiment_counts": {},
                    "raw_flag_counts": {},
                    "normalized_flag_counts": {},
                    "fallback_reason_counts": {},
                    "tag_extract_failed_count": len(histories),
                    "parse_low_confidence_count": len(histories),
                }
            )
        return {item.target_date: neutral_label(item.target_date) for item in histories}

    warnings: list[str] = []
    monkeypatch.setattr(generator, "label_notes_in_batches", _fake_labeler)
    monkeypatch.setattr(generator.logging, "warning", lambda msg, *args: warnings.append(msg % args if args else msg))
    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    assert result is not None
    assert any("non_empty_but_no_signals" in line for line in warnings)


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
    assert "これは最終本文です。" in result.judgment_json["analysis_audit"]["final_text"]["text"]


def test_sleep_duration_zero_marked_invalid() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    h = _summary(1, sleep_start=None, sleep_end=None, sleep_duration_min=0, sleep_score=0)
    df = build_daily_feature_table([h], {})
    assert bool(df.iloc[0]["sleep_valid_flag"]) is False
    assert df.iloc[0]["sleep_invalid_reason"] == "zero_duration_and_score_zero"


def test_invalid_sleep_not_treated_as_short_sleep() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    h = _summary(1, sleep_start=None, sleep_end=None, sleep_duration_min=0, sleep_score=0)
    df = build_daily_feature_table([h], {})
    assert bool(df.iloc[0]["sleep_lt_6h_flag"]) is False


def test_invalid_sleep_excluded_from_sleep_lag_features() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    hs = [_summary(1, sleep_start=None, sleep_end=None, sleep_duration_min=0, sleep_score=0), _summary(2, sleep_duration_min=420)]
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
    hs = [_summary(i, sleep_duration_min=420) for i in range(1, 8)] + [_summary(8, sleep_start=None, sleep_end=None, sleep_duration_min=0, sleep_score=0)]
    df = build_daily_feature_table(hs, {})
    payload = build_analysis_json(
        target_date="2026-03-08",
        today_summary=_summary(8, sleep_start=None, sleep_end=None, sleep_duration_min=0, sleep_score=0),
        features_df=df,
        exploratory_summary=analyze_exploratory_patterns(df),
        regression_summary={"available": False, "sample_size": 0},
        lightgbm_summary={"available": False, "sample_size": 0},
    )
    assert bool(payload["today_sleep_context"]["sleep_available"]) is False


def test_zero_duration_score_zero_record_does_not_crush_valid_candidate() -> None:
    import scripts.mood_advice_generator as generator

    today = _summary(26, target_date="2026-03-26", sleep_start=None, sleep_end=None, sleep_duration_min=0, sleep_score=0)
    valid = _summary(27, target_date="2026-03-27", sleep_start="2026-03-27T01:35:00+09:00", sleep_end="2026-03-27T08:17:00+09:00", sleep_duration_min=0, sleep_score=75)
    _, selected, source = generator._resolve_today_sleep_candidates(target_date="2026-03-26", today_summary=today, history=[valid])
    assert selected is not None
    assert source == "history_target_date_match"
    assert bool(selected["candidate_valid_flag"]) is True


def test_saved_sleep_properties_are_prioritized_for_today_advice_context() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    today = _summary(
        26,
        target_date="2026-03-26",
        sleep_start="2026-03-27T01:35:00+09:00",
        sleep_end="2026-03-27T08:17:00+09:00",
        sleep_duration_min=0,
        sleep_score=75,
    )
    df = build_daily_feature_table([today], {})
    payload = build_analysis_json(
        target_date="2026-03-26",
        today_summary=today,
        features_df=df,
        exploratory_summary={"matched_today_conditions": []},
        regression_summary={"available": False, "sample_size": 0},
        lightgbm_summary={"available": False, "sample_size": 0},
        today_sleep_context={"sleep_available": True, "sleep_hours": 6.7, "sleep_score": 75, "duration_source": "selected_sleep_candidate"},
    )
    assert bool(payload["today_sleep_context"]["sleep_available"]) is True
    assert payload["today_sleep_context"]["sleep_hours"] == 6.7


def test_renderer_sleep_context_is_single_source_of_truth() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    today = _summary(
        28,
        target_date="2026-03-28",
        sleep_start=None,
        sleep_end=None,
        sleep_duration_min=0,
        sleep_score=0,
    )
    df = build_daily_feature_table([today], {})
    payload = build_analysis_json(
        target_date="2026-03-28",
        today_summary=today,
        features_df=df,
        exploratory_summary={"matched_today_conditions": []},
        regression_summary={"available": False, "sample_size": 0},
        lightgbm_summary={"available": False, "sample_size": 0},
        today_sleep_context={"sleep_available": True, "sleep_hours": 13.0, "sleep_score": 82, "duration_source": "selected_sleep_candidate"},
    )
    assert bool(payload["today_sleep_context"]["sleep_available"]) is True
    assert payload["today_sleep_context"]["sleep_hours"] == 13.0


def test_feature_builder_no_fragmentation_warning() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    histories = [_summary(i) for i in range(1, 20)]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _ = build_daily_feature_table(histories, {})
    assert not any("highly fragmented" in str(item.message) for item in caught)


def test_render_today_advice_sleep_optional_no_forced_prefix() -> None:
    text = render_today_advice_from_analysis(
        analysis_json={
            "today_sleep_context": {"sleep_available": True, "sleep_hours": 6.7, "sleep_should_mention": False},
            "matched_patterns_count": 1,
            "primary_focus": "負荷調整",
        },
        model="x",
        chat_completion=lambda **kwargs: "今日はまず進行中タスクを2件終わらせましょう。",
    )
    assert "睡眠" not in text


def test_render_today_advice_allows_sleep_when_delta_is_large() -> None:
    text = render_today_advice_from_analysis(
        analysis_json={
            "today_sleep_context": {"sleep_available": True, "sleep_hours": 6.7, "sleep_should_mention": True},
            "matched_patterns_count": 1,
            "primary_focus": "負荷調整",
        },
        model="x",
        chat_completion=lambda **kwargs: "睡眠が長めなので午前に難しい判断を進められます。",
    )
    assert "睡眠" in text


def test_real_case_like_payload_does_not_return_sleep_unknown(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator

    target = _summary(
        26,
        target_date="2026-03-26",
        sleep_start=None,
        sleep_end=None,
        sleep_duration_min=0,
        sleep_score=0,
    )
    next_day = _summary(
        27,
        target_date="2026-03-27",
        sleep_start="2026-03-27T01:35:00+09:00",
        sleep_end="2026-03-27T08:17:00+09:00",
        sleep_duration_min=268,
        sleep_score=75,
    )
    histories = [_summary(i, target_date=f"2026-03-{i:02d}") for i in range(1, 20)]
    monkeypatch.setattr(generator, "load_daily_logs_for_period", lambda **kwargs: [next_day, target, *histories])
    monkeypatch.setattr(generator, "_chat_completion", lambda **kwargs: "今日は睡眠を踏まえて負荷を調整してください。")

    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-26")
    assert result is not None
    assert "睡眠データ不明" not in result.today_advice


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
    assert "午前の判断負荷" in text and "最初の一手" in text
    assert "限定的" in text or "過去傾向" in text
