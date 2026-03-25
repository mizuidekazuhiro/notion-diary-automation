from __future__ import annotations

import json
import importlib.util

import pytest
from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts.note_batch_labeler import label_notes_in_batches, parse_note_label_json
from scripts.today_advice_feature_builder import build_daily_feature_table
from scripts.today_advice_pattern_analyzer import analyze_lag_patterns
from scripts.today_advice_regression import run_low_mood_regression
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
    assert parsed[0].sentiment_label == "negative"
    assert parsed[0].fatigue_flag is True


def test_note_empty_fallback_neutral() -> None:
    items = [_summary(1, notes="")]
    result = label_notes_in_batches(summaries=items, model="m", chat_completion=lambda **kwargs: "[]")
    assert result["2026-03-01"].sentiment_label == "neutral"
    assert result["2026-03-01"].confidence == "low"


def test_lag_pattern_decision_thresholds() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    histories = [_summary(i, mood="★" if i % 2 == 0 else "★★★★", notes="疲れ") for i in range(1, 12)]
    labels = {h.target_date: parse_note_label_json('[{"date":"%s","fatigue_flag":true,"sentiment_label":"negative","sentiment_score":-1}]' % h.target_date, [{"date": h.target_date, "notes": h.notes}])[0] for h in histories}
    df = build_daily_feature_table(histories, labels)
    result = analyze_lag_patterns(df)
    assert len(result["all_patterns"]) >= 8
    assert all("confidence" in item for item in result["all_patterns"])


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
    payload = build_analysis_json(target_date="2026-03-10", today_summary=_summary(10), features_df=df, adopted_patterns=[], regression_summary={"available": False, "sample_size": 0})
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
        analysis_json={"matched_patterns": [], "evidence_used": [], "today_sleep_context": {"sleep_hours": 6.2}},
        model="x",
        chat_completion=lambda **kwargs: "一般論です。",
    )
    assert text == "過去30日で明確な再現パターンは不明です。"


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
        "lag_analysis",
        "regression",
        "today_match",
        "analysis_json",
        "final_text",
        "notes_fallback_reason_counts",
        "sleep_feature_conversion_samples",
        "matched_patterns_count",
        "evidence_used",
    ]:
        assert key in audit


def test_lag_analysis_is_included_in_audit_log(monkeypatch) -> None:
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
    lag = result.judgment_json["analysis_audit"]["lag_analysis"]
    assert lag["evaluated_count"] > 0
    assert any("pattern_id" in item and "target_outcome" in item for item in lag["patterns"])


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
