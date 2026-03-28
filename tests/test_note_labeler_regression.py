from __future__ import annotations

import importlib.util
import json

import pytest

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts.note_batch_labeler import (
    label_notes_in_batches,
    parse_note_label_json,
    parse_note_label_json_with_meta,
)
from scripts.today_advice_feature_builder import build_daily_feature_table

HAS_PANDAS = importlib.util.find_spec("pandas") is not None


def _summary(target_date: str, notes: str) -> DailyLogSummary:
    return DailyLogSummary(
        target_date=target_date,
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
        done_count=1,
        done_tasks=[],
        done_tasks_detail=[],
        drop_count=0,
        drop_tasks=[],
        kcal=None,
        protein=None,
        fat=None,
        carb=None,
        expenses_total=0,
        expenses=ExpenseSummary(total=0, count=0, top=[], remaining=0),
        location_summary=None,
        mood="★★★",
        notes=notes,
        weight=None,
        sleep_start="2026-03-01T01:00:00+09:00",
        sleep_end="2026-03-01T07:00:00+09:00",
        sleep_duration_min=360,
        resolved_sleep_duration_min=360,
        resolved_sleep_duration_hours=6.0,
        resolved_sleep_duration_text="6時間0分",
        sleep_duration_source="derived_from_start_end",
        sleep_score=70,
        sleep_source=None,
        readiness_stars=None,
        readiness_hrv=None,
        readiness_bpm=None,
        baseline_hrv=None,
        baseline_waking_bpm=None,
        sleep_heart_rate=None,
        deep_duration_min=90,
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


def test_tags_only_output_is_normalized_to_signals() -> None:
    raw = json.dumps(
        [{"date": "2026-03-20", "tags": ["fatigue", "late_work"], "meta": {"parse_quality": "medium"}}],
        ensure_ascii=False,
    )
    parsed = parse_note_label_json(raw, [{"date": "2026-03-20", "notes": "疲れた、遅くまで仕事"}])[0]
    tags = {item["tag"] for item in parsed.signals}
    assert {"fatigue", "late_work"}.issubset(tags)
    assert parsed.tag_extract_failed is False
    assert parsed.no_signal_note is False


def test_signals_output_keeps_backward_compatibility() -> None:
    raw = json.dumps(
        [
            {
                "date": "2026-03-20",
                "signals": [
                    {"tag": "conflict", "category": "state", "polarity": "negative", "intensity": "high", "confidence": 0.9, "evidence_text": "喧嘩"}
                ],
                "meta": {"parse_quality": "high"},
            }
        ],
        ensure_ascii=False,
    )
    parsed = parse_note_label_json(raw, [{"date": "2026-03-20", "notes": "喧嘩した"}])[0]
    assert parsed.stress_flag is False
    assert any(s["tag"] == "conflict" for s in parsed.signals)


@pytest.mark.parametrize("raw", [
    '{"rows":[{"date":"2026-03-20","tags":["gym"]}]}',
    '{"results":[{"date":"2026-03-20","tags":["gym"]}]}'
])
def test_parser_accepts_wrapper_objects(raw: str) -> None:
    parsed, meta = parse_note_label_json_with_meta(raw, [{"date": "2026-03-20", "notes": "ジム"}])
    assert meta["schema_mismatch"] is False
    assert any(s["tag"] == "gym" for s in parsed[0].signals)


def test_representative_notes_expected_tags() -> None:
    cases = [
        ("仕事はそこそこ。夜は家に帰りジム。", ["moderate_productivity", "early_home", "gym"]),
        ("飲み会、食事若干乱れ。電車で帰宅、お金セーブ。", ["social", "drinking", "meal_disruption", "commute_train", "money_saved"]),
        ("大阪から帰ってきてすごく疲れた。22時まで仕事。", ["fatigue", "late_work", "business_trip"]),
        ("ゆいさんと喧嘩。鍵を家に忘れた。", ["conflict", "regret"]),
    ]
    for idx, (note, expected_tags) in enumerate(cases, start=20):
        raw = json.dumps([{"date": f"2026-03-{idx}", "tags": expected_tags}], ensure_ascii=False)
        label = parse_note_label_json(raw, [{"date": f"2026-03-{idx}", "notes": note}])[0]
        got = {sig["tag"] for sig in label.signals}
        assert set(expected_tags).issubset(got)


def test_feature_builder_flags_from_note_labels() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    items = [
        _summary("2026-03-20", "運動した"),
        _summary("2026-03-21", "飲み会"),
        _summary("2026-03-22", "喧嘩"),
        _summary("2026-03-23", "疲れた"),
    ]
    raws = {
        "2026-03-20": '[{"date":"2026-03-20","tags":["exercise"]}]',
        "2026-03-21": '[{"date":"2026-03-21","tags":["drinking","social","money_saved"]}]',
        "2026-03-22": '[{"date":"2026-03-22","tags":["conflict"]}]',
        "2026-03-23": '[{"date":"2026-03-23","tags":["fatigue"]}]',
    }
    labels = {
        day: parse_note_label_json(raw, [{"date": day, "notes": "x"}])[0]
        for day, raw in raws.items()
    }
    df = build_daily_feature_table(items, labels)
    assert int(df["notes_fatigue_flag"].sum()) >= 1
    assert int(df["notes_has_exercise"].sum()) >= 1
    assert int(df["notes_has_drinking"].sum()) >= 1
    assert int(df["notes_has_conflict"].sum()) >= 1
    assert int(df["notes_has_money_saved"].sum()) >= 1


def test_audit_metrics_include_failure_and_top_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTES_LABEL_CACHE_DISABLE", "1")
    summaries = [_summary("2026-03-20", "疲れた"), _summary("2026-03-21", "")]
    responses = iter(["not-json", '[{"date":"2026-03-20","tags":["fatigue"]}]'])

    def _chat_completion(**kwargs: object) -> str:
        return next(responses)

    audit: dict[str, object] = {}
    labels = label_notes_in_batches(summaries=summaries, chat_completion=_chat_completion, model="x", audit=audit)
    assert labels["2026-03-20"].tag_extract_failed is False
    assert int(audit.get("parse_error_count", 0)) >= 1
    assert int(audit.get("signals_detected_count", 0)) >= 1
    assert isinstance(audit.get("top_tags"), list) and len(audit.get("top_tags", [])) >= 1
