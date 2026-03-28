from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts import daily_job
from scripts.expense_f_aggregator import aggregate_daily_expense_f
from scripts import f_risk_generator

HAS_PANDAS = importlib.util.find_spec("pandas") is not None


def _summary(day: int, **overrides: object) -> DailyLogSummary:
    payload = dict(
        target_date=f"2026-03-{day:02d}",
        date=f"2026-03-{day:02d}",
        target_date_value=f"2026-03-{day:02d}",
        page_id="page",
        title="Daily Log",
        summary_text="",
        summary_html="",
        mail_id="run",
        source="automation",
        diary=None,
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
        location_summary="自宅中心",
        mood="★★★",
        notes="メモ",
        weight=None,
        sleep_start="2026-03-01T00:30:00+09:00",
        sleep_end="2026-03-01T07:30:00+09:00",
        sleep_duration_min=420,
        resolved_sleep_duration_min=420,
        resolved_sleep_duration_hours=7.0,
        resolved_sleep_duration_text="7時間00分",
        sleep_duration_source="derived_from_start_end",
        sleep_score=75,
        sleep_source="AutoSleep",
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
        page_url="https://example.com/page",
        diary_notification_sent=None,
    )
    payload.update(overrides)
    return DailyLogSummary(**payload)


def test_expense_f_missing_env_includes_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("EXPENSES_DB_ID", raising=False)

    result = aggregate_daily_expense_f("2026-03-20")

    assert result.available is False
    assert result.skip_reason == "expenses_data_unavailable"
    assert result.debug_summary["reason"] == "missing_env"
    assert sorted(result.debug_summary["missing"]) == ["EXPENSES_DB_ID", "NOTION_TOKEN"]


def test_f_risk_note_labeler_signature_and_no_typeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import pandas as pd

    histories = [_summary(i) for i in range(1, 14)]
    captured: dict[str, object] = {}

    def _fake_labeler(*, summaries, chat_completion, model, **kwargs):
        captured["summaries"] = summaries
        captured["chat_completion"] = chat_completion
        captured["model"] = model
        return {item.target_date: object() for item in summaries}

    def _fake_build_table(_histories, _labels):
        dates = [h.target_date for h in _histories]
        return pd.DataFrame(
            {
                "date": dates,
                "expense_f_count": [1 if i % 3 == 0 else 0 for i in range(len(dates))],
                "sleep_hours": [7.0] * len(dates),
                "sleep_score": [70] * len(dates),
                "spending_total": [1000] * len(dates),
                "weather_temp_max_c": [20] * len(dates),
                "weather_precip_probability_max": [10] * len(dates),
                "task_drop_count": [0] * len(dates),
                "notes_stress_flag": [0] * len(dates),
                "is_weekend": [0] * len(dates),
            }
        )

    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: histories)
    monkeypatch.setattr(f_risk_generator, "label_notes_in_batches", _fake_labeler)
    monkeypatch.setattr(f_risk_generator, "build_daily_feature_table", _fake_build_table)
    monkeypatch.setattr(f_risk_generator, "_fit_model", lambda train, today: {"skipped_reason": "ml_lib_not_installed"})

    result = f_risk_generator.generate_f_risk(
        daily_log_read_url="read-url",
        bearer_token=None,
        target_date="2026-03-20",
    )

    assert result.skip_reason == "ml_lib_not_installed"
    assert captured["summaries"] == histories
    assert callable(captured["chat_completion"])
    assert captured["model"] == "gpt-4.1-mini"


def test_notify_diary_continues_when_f_risk_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    summary = _summary(20, target_date="2026-03-20", diary="generated diary")
    config = SimpleNamespace(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_weather", lambda config, *, summary, run_id: order.append("weather") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_expense_f", lambda config, *, summary, run_id: order.append("expense_f") or {"matched": False, "reasons": []})
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", lambda config, *, summary, run_id: order.append("sleep") or summary)
    monkeypatch.setattr(
        daily_job,
        "_generate_and_save_f_risk",
        lambda config, *, summary, run_id: (_ for _ in ()).throw(RuntimeError("f risk failed")),
    )
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", lambda config, *, summary, run_id: order.append("advice") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", lambda config, *, summary, run_id: order.append("diary") or summary)
    monkeypatch.setattr(daily_job, "_notify_phase_c", lambda config, *, summary, run_id: order.append("notify") or False)

    daily_job.run_notify_diary(config, "2026-03-20", "run")

    assert order == ["weather", "expense_f", "sleep", "advice", "diary", "notify"]
