from __future__ import annotations

import importlib
import re
import types

import pandas as pd
import pytest

from publish.email_templates import render_daily_log_html, render_daily_log_text
from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from publish.render_mail import render_mail
from scripts.daily_job import build_diary_input_fields
from scripts.mail_dedupe import build_mail_input_snapshot
from scripts.note_batch_labeler import parse_note_label_json_with_meta
from scripts.today_advice_lightgbm import run_lightgbm_low_mood
from scripts.today_advice_renderer import render_today_advice_from_analysis


def _summary(**overrides: object) -> DailyLogSummary:
    base = dict(
        target_date="2026-03-27",
        date="2026-03-27",
        target_date_value="2026-03-27",
        page_id="page",
        title="Daily Log｜2026-03-27",
        summary_text="🎉 昨日完了したこと（Done: 1）\n- Ship (Priority: High)\n🧹 昨日手放したこと（Drop: 0）\n- None",
        summary_html="",
        mail_id="run",
        source="automation",
        diary="日記",
        meal_summary="meal",
        meal_photos=[],
        place=None,
        activity_summary=None,
        done_count=1,
        done_tasks=["Ship"],
        done_tasks_detail=[],
        drop_count=0,
        drop_tasks=[],
        kcal=None,
        protein=None,
        fat=None,
        carb=None,
        expenses_total=1000,
        expenses=ExpenseSummary(total=1000, count=1, top=[], remaining=0),
        location_summary="自宅中心",
        mood="★★★",
        notes="",
        weight=None,
        sleep_start="2026-03-27T01:35:00+09:00",
        sleep_end="2026-03-27T08:17:00+09:00",
        sleep_duration_min=268.0,
        resolved_sleep_duration_min=402.0,
        resolved_sleep_duration_hours=6.7,
        resolved_sleep_duration_text="6時間42分",
        sleep_duration_source="derived_from_start_end",
        sleep_score=70,
        sleep_source="apple_health",
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
        today_advice="today advice",
        study_minutes=150.0,
        study_sessions=4,
        study_last_used_at="2026-03-27T23:42:00+09:00",
        diary_input_hash=None,
        today_advice_input_hash=None,
        diary_generated_at=None,
        today_advice_generated_at=None,
        page_url=None,
        diary_notification_sent=None,
    )
    base.update(overrides)
    return DailyLogSummary(**base)


def _payload(summary: DailyLogSummary) -> dict[str, object]:
    return {
        "target_date": summary.target_date,
        "run_id": summary.mail_id,
        "summary_text": summary.summary_text,
        "done_count": summary.done_count,
        "drop_count": summary.drop_count,
        "diary": summary.diary,
        "meal_summary": summary.meal_summary,
        "meal_photos": summary.meal_photos,
        "expenses_total": summary.expenses_total,
        "expenses": {"total": summary.expenses.total, "count": summary.expenses.count, "top": [], "remaining": 0},
        "location_summary": summary.location_summary,
        "mood": summary.mood,
        "weight": summary.weight,
        "today_advice": summary.today_advice,
        "study_minutes": summary.study_minutes,
        "study_sessions": summary.study_sessions,
        "study_last_used_at": summary.study_last_used_at,
        "sleep_start": summary.sleep_start,
        "sleep_end": summary.sleep_end,
        "sleep_duration_min": summary.resolved_sleep_duration_min,
    }


def test_sleep_duration_is_canonical_across_diary_and_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _summary()
    used, _, _, _ = build_diary_input_fields(summary)
    assert used["Sleep Duration"] == "402.0"

    text = render_daily_log_text(_payload(summary))
    html = render_daily_log_html(_payload(summary))
    assert "睡眠時間: 6時間42分" in text
    assert "6時間42分" in html

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("MAIL_LINK_SECRET", "secret")
    mail = render_mail(summary)
    assert "6時間42分" in mail.plain_text
    assert "6時間42分" in mail.html_body


def test_drop_zero_with_none_placeholder_stays_zero() -> None:
    rendered = render_daily_log_text(_payload(_summary()))
    assert "Drop: 0" in rendered
    assert "Drop: 1" not in rendered
    assert "- None" not in rendered


def test_html_and_text_section_order_is_identical() -> None:
    payload = _payload(_summary())
    text = render_daily_log_text(payload)
    html = render_daily_log_html(payload)
    text_order = [
        "Today advice",
        "Diary",
        "Summary",
        "Sleep & Condition",
        "Expenses (昨日の支出)",
        "🎉 昨日完了したこと",
        "🧹 昨日手放したこと",
        "🍽️ Meal summary",
    ]
    for first, second in zip(text_order, text_order[1:]):
        assert text.index(first) < text.index(second)
        assert html.index(first.replace("&", "&amp;")) < html.index(second.replace("&", "&amp;"))


def test_study_section_rendered_in_text_and_html() -> None:
    payload = _payload(_summary(study_minutes=150.0, study_sessions=4, study_last_used_at="2026-05-05T23:42:00+09:00"))
    text = render_daily_log_text(payload)
    html = render_daily_log_html(payload)
    assert "司法試験 Study" in text
    assert "2.5時間（150分）" in text
    assert "最終利用: 23:42" in text
    assert "司法試験 Study" in html
    assert "2.5時間（150分）" in html


def test_study_section_hidden_when_study_minutes_is_none() -> None:
    payload = _payload(_summary(study_minutes=None, study_sessions=4))
    assert "司法試験 Study" not in render_daily_log_text(payload)
    assert "司法試験 Study" not in render_daily_log_html(payload)


def test_mail_snapshot_includes_study_fields() -> None:
    summary = _summary()
    snapshot = build_mail_input_snapshot(summary, expense_f_alert={}, f_risk_alert={})
    assert snapshot["study_minutes"] == 150
    assert snapshot["study_sessions"] == 4
    assert snapshot["study_last_used_at"] == "2026-03-27T23:42:00+09:00"


def test_diary_input_fields_include_study_fields() -> None:
    summary = _summary()
    used, _, _, _ = build_diary_input_fields(summary)
    assert used["Study Minutes"] == "150.0"
    assert used["Study Sessions"] == "4"
    assert used["Study Last Used At"] == "2026-03-27T23:42:00+09:00"


def test_today_advice_fallback_is_dense() -> None:
    text = render_today_advice_from_analysis(
        analysis_json={
            "today_sleep_context": {"sleep_available": True, "sleep_hours": 6.7, "sleep_should_mention": False},
            "recent_7d_summary": {"behavior_trend": ["直近7日で夜遅い外出が2回", "外出は週後半に偏りがありました"]},
            "primary_focus": "負荷調整",
            "data_quality": {"notes_label_quality": {"label_quality_low": True, "notes_parse_success_rate": 0.3}},
        },
        model="x",
        chat_completion=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    sentence_count = len([s for s in re.split(r"。", text) if s.strip()])
    assert len(text) >= 240
    assert sentence_count <= 6
    assert "直近7日" in text and "最初の一手" in text
    assert "品質が低い" not in text
    assert "parse" not in text
    assert "unknown" not in text


def test_note_date_matching_normalizes_multiple_formats() -> None:
    raw = '{"rows":[{"date":" 2026/03/25 ","tags":["gym"]},{"date":"2026-03-25T00:00:00Z","tags":["fatigue"]},{"date":"2026-03-25","tags":["stress"]}]}'
    parsed, meta = parse_note_label_json_with_meta(raw, [{"date": "2026-03-25", "notes": "ジム"}])
    assert meta["schema_mismatch"] is False
    assert parsed[0].date == "2026-03-25"
    assert meta["matched_dates"] == {"2026-03-25"}


def test_diary_input_ignores_stale_sleep_analysis_duration() -> None:
    summary = _summary(
        sleep_analysis_jp="深夜1時35分から朝8時17分までの約4時間28分でした。",
        today_condition_forecast_jp="今日は4時間28分睡眠の影響で集中に波が出る見込みです。",
    )
    used, _, _, _ = build_diary_input_fields(summary)
    assert used["Sleep Duration"] == "402.0"
    assert used["Sleep Duration Text"] == "6時間42分"
    assert "Sleep Analysis JP" not in used
    assert "Today Condition Forecast JP" not in used


def test_diary_input_excludes_generated_fields_and_keeps_notes() -> None:
    summary = _summary(today_advice="朝は重い判断を後ろ倒しにする", notes="夜更かしした")
    used, _, _, _ = build_diary_input_fields(summary)
    assert "Today advice" not in used
    assert "Sleep Analysis JP" not in used
    assert "Today Condition Forecast JP" not in used
    assert used["Notes"] == "夜更かしした"


def test_lightgbm_preprocess_drops_unsupported_object_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyLGBM:
        feature_importances_ = []

        def __init__(self, **kwargs: object) -> None:
            self.feature_importances_ = []

        def fit(self, x: pd.DataFrame, y: pd.Series) -> None:
            self.feature_importances_ = [1] * len(x.columns)

        def predict_proba(self, x: pd.DataFrame):
            return [[0.2, 0.8]]

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(importlib, "import_module", lambda name: types.SimpleNamespace(LGBMClassifier=DummyLGBM))

    rows = []
    for i in range(15):
        rows.append({
            "date": f"2026-03-{i+1:02d}",
            "mood": 1 if i % 2 == 0 else 5,
            "notes_sentiment_label": "positive" if i % 3 else "negative",
            "unsupported_text": "abc",
            "flag": bool(i % 2),
            "num": float(i),
        })
    df = pd.DataFrame(rows)
    out = run_lightgbm_low_mood(df)
    assert "unsupported_text" in out["skipped_columns"]
    assert "unsupported_text" not in out["feature_columns"]
