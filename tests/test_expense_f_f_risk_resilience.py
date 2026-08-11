from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from publish.read_daily_log import DoneTaskDetail
from scripts import daily_job
from scripts.expense_f_aggregator import aggregate_daily_expense_f
from scripts import f_risk_generator
from scripts.note_batch_labeler import NoteLabel

HAS_PANDAS = importlib.util.find_spec("pandas") is not None


@pytest.fixture(autouse=True)
def _isolate_github_actions_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent from the runner's infrastructure environment."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


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


def test_f_risk_expense_hydration_fails_closed_without_credentials_in_github_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("EXPENSES_DB_ID", raising=False)

    result = f_risk_generator._hydrate_expense_f_from_expenses_db([_summary(1)])

    assert result[0].expense_f_data_status == "query_failed"


def test_expense_f_missing_env_includes_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("EXPENSES_DB_ID", raising=False)

    result = aggregate_daily_expense_f("2026-03-20")

    assert result.available is False
    assert result.skip_reason == "expenses_data_unavailable"
    assert result.debug_summary["reason"] == "missing_env"
    assert sorted(result.debug_summary["missing"]) == ["EXPENSES_DB_ID", "NOTION_TOKEN"]


def test_expense_f_uses_default_props_and_ignores_category(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("EXPENSES_DB_ID", "db")
    monkeypatch.delenv("EXPENSE_CATEGORY_PROP", raising=False)
    monkeypatch.setattr(
        "scripts.expense_f_aggregator.requests.get",
        lambda *args, **kwargs: _Resp({"properties": {"F": {}, "Date": {}, "Received At": {}, "Merchant": {}, "Amount": {}, "Category": {}}}),
    )
    monkeypatch.setattr(
        "scripts.expense_f_aggregator.requests.post",
        lambda *args, **kwargs: _Resp(
            {
                "results": [
                    {
                        "created_time": "2026-03-20T09:00:00+09:00",
                        "properties": {
                            "Amount": {"type": "number", "number": 1234},
                            "Merchant": {"type": "rich_text", "rich_text": [{"plain_text": "Shop"}]},
                            "Received At": {"type": "date", "date": {"start": "2026-03-20T09:00:00+09:00"}},
                        }
                    }
                ],
                "has_more": False,
            }
        ),
    )

    result = aggregate_daily_expense_f("2026-03-20")

    assert result.available is True
    assert result.count == 1
    assert result.total == 1234
    assert result.debug_summary["resolved_props"]["category"]["resolved_name"] == "Category"


def test_expense_f_schema_alias_japanese_names(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("EXPENSES_DB_ID", "db")
    for key in ["EXPENSE_F_PROP", "EXPENSE_DATE_PROP", "EXPENSE_RECEIVED_AT_PROP", "EXPENSE_MERCHANT_PROP", "EXPENSE_AMOUNT_PROP", "EXPENSE_CATEGORY_PROP"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "scripts.expense_f_aggregator.requests.get",
        lambda *args, **kwargs: _Resp({"properties": {"F判定": {}, "日付": {}, "受領日時": {}, "店名": {}, "金額": {}, "費目": {}}}),
    )
    monkeypatch.setattr(
        "scripts.expense_f_aggregator.requests.post",
        lambda *args, **kwargs: _Resp({"results": [], "has_more": False}),
    )
    result = aggregate_daily_expense_f("2026-03-20")
    assert result.data_status == "no_results"
    assert result.debug_summary["resolved_props"]["f"]["resolved_name"] == "F判定"


def test_expense_f_schema_unresolved_distinguishes_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("EXPENSES_DB_ID", "db")
    monkeypatch.setattr("scripts.expense_f_aggregator.requests.get", lambda *args, **kwargs: _Resp({"properties": {"日付": {}, "店名": {}, "金額": {}}}))
    result = aggregate_daily_expense_f("2026-03-20")
    assert result.data_status == "schema_unresolved"


def test_f_risk_note_labeler_signature_and_no_typeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import pandas as pd

    histories = [_summary(i) for i in range(1, 20)]
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
    monkeypatch.setattr(f_risk_generator, "_fit_model", lambda train, today, **kwargs: {"skipped_reason": "ml_lib_not_installed"})

    result = f_risk_generator.generate_f_risk(
        daily_log_read_url="read-url",
        bearer_token=None,
        target_date="2026-03-20",
    )

    assert result.skip_reason in {"insufficient_samples", "model_unavailable", None}
    assert [item.target_date for item in captured["summaries"]] == [item.target_date for item in histories]
    assert callable(captured["chat_completion"])
    assert captured["model"] == "gpt-4.1-mini"


def test_notify_diary_continues_when_f_risk_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    summary = _summary(20, target_date="2026-03-20", diary="generated diary")
    config = SimpleNamespace(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_weather", lambda config, *, summary, run_id: order.append("weather") or summary)
    monkeypatch.setattr(daily_job, "_compute_expense_f_alert", lambda *, summary, run_id: order.append("expense_f") or {"matched": False, "reasons": []})
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", lambda config, *, summary, run_id: order.append("sleep") or summary)
    monkeypatch.setattr(
        daily_job,
        "_generate_and_save_f_risk",
        lambda config, *, summary, run_id, **kwargs: (_ for _ in ()).throw(RuntimeError("f risk failed")),
    )
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", lambda config, *, summary, run_id: order.append("advice") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", lambda config, *, summary, run_id, **kwargs: order.append("diary") or summary)
    daily_job.run_notify_diary(config, "2026-03-20", "run")

    assert order == ["weather", "expense_f", "sleep", "advice", "diary"]


def test_f_risk_uses_multi_domain_features_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import pandas as pd

    histories = [_summary(i) for i in range(1, 21)]

    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: histories)
    monkeypatch.setattr(
        f_risk_generator,
        "label_notes_in_batches",
        lambda **kwargs: {h.target_date: object() for h in histories},
    )
    monkeypatch.setattr(
        f_risk_generator,
        "build_daily_feature_table",
        lambda _h, _l: pd.DataFrame(
            {
                "date": [h.target_date for h in histories],
                "expense_f_count": [1 if i % 5 == 0 else 0 for i in range(len(histories))],
                "expense_f_total": [3000 if i % 5 == 0 else 0 for i in range(len(histories))],
                "sleep_hours": [5.5] * len(histories),
                "sleep_score": [65] * len(histories),
                "sleep_short_streak": [2] * len(histories),
                    "notes_stress_flag": [1] * len(histories),
                    "notes_social_load_flag": [1] * len(histories),
                    "notes_fatigue_flag": [1] * len(histories),
                    "notes_has_drinking": [1] * len(histories),
                    "notes_present_flag": [1] * len(histories),
                    "notes_signal_count": [3] * len(histories),
                "kcal": [2300] * len(histories),
                "fat": [90] * len(histories),
                "kcal_vs_7d_delta": [420] * len(histories),
                "fat_vs_7d_delta": [20] * len(histories),
                "spending_total": [8000] * len(histories),
                "spending_vs_7d_delta": [3500] * len(histories),
                "task_done_count": [1] * len(histories),
                "task_drop_count": [3] * len(histories),
                "task_completion_ratio": [0.25] * len(histories),
                "location_present_flag": [1] * len(histories),
                "late_outing_flag": [1] * len(histories),
                "multi_stop_flag": [0] * len(histories),
                "weather_retrieved_flag": [1] * len(histories),
                "weather_code": [61] * len(histories),
                "weather_precip_probability_max": [90] * len(histories),
                "schedule_signal_available_flag": [1] * len(histories),
                "schedule_same_day_event_count": [3] * len(histories),
                "is_weekend": [1] * len(histories),
            }
        ),
    )
    monkeypatch.setattr(f_risk_generator, "_fit_model", lambda *args, **kwargs: {"skipped_reason": "ml_lib_not_installed"})
    monkeypatch.setattr(f_risk_generator, "_render_f_risk_alert", lambda **kwargs: ("fallback alert", True, None))

    result = f_risk_generator.generate_f_risk(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    risk_json = result.debug_summary["risk_json"]
    assert result.skip_reason is None
    assert risk_json["fallback_used"] is True
    assert risk_json["risk_matched"] is True
    assert {"sleep", "meal", "spending", "tasks", "notes", "weather", "location"}.issubset(set(risk_json["input_groups_available"]))


def test_f_risk_schedule_unavailable_does_not_crash_and_logs_exclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import pandas as pd

    histories = [_summary(i) for i in range(1, 21)]
    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: histories)
    monkeypatch.setattr(f_risk_generator, "label_notes_in_batches", lambda **kwargs: {h.target_date: object() for h in histories})
    monkeypatch.setattr(
        f_risk_generator,
        "build_daily_feature_table",
        lambda _h, _l: pd.DataFrame(
            {
                "date": [h.target_date for h in histories],
                "expense_f_count": [1 if i % 4 == 0 else 0 for i in range(len(histories))],
                "sleep_hours": [7.0] * len(histories),
                "sleep_score": [75] * len(histories),
                "spending_total": [1000] * len(histories),
                "task_done_count": [2] * len(histories),
                "task_drop_count": [0] * len(histories),
                "notes_present_flag": [1] * len(histories),
                "location_present_flag": [1] * len(histories),
                "weather_retrieved_flag": [1] * len(histories),
                "weather_precip_probability_max": [20] * len(histories),
                "is_weekend": [0] * len(histories),
            }
        ),
    )
    monkeypatch.setattr(f_risk_generator, "_fit_model", lambda *args, **kwargs: {"skipped_reason": "ml_lib_not_installed"})
    result = f_risk_generator.generate_f_risk(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    risk_json = result.debug_summary["risk_json"]
    assert result.skip_reason is None
    assert "schedule" in risk_json["input_groups_unavailable"]
    assert risk_json["excluded_reasons"]["schedule"] == "unavailable_from_existing_read_path"


def test_feature_builder_does_not_treat_future_event_as_same_day() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    from scripts.today_advice_feature_builder import build_daily_feature_table

    summary = _summary(
        20,
        done_tasks_detail=[
            DoneTaskDetail(title="same day", done_date="2026-03-20", event_date="2026-03-20"),
            DoneTaskDetail(title="future", done_date="2026-03-20", event_date="2026-03-22"),
        ],
    )
    labels = {
        summary.target_date: NoteLabel(
            date=summary.target_date,
            sentiment_label="neutral",
            sentiment_score=0,
            fatigue_flag=False,
            stress_flag=False,
            social_load_flag=False,
            achievement_flag=False,
            self_care_flag=False,
            sleep_issue_flag=False,
            confidence="medium",
            evidence_keywords=[],
        )
    }
    df = build_daily_feature_table([summary], labels)
    row = df.iloc[0]
    assert row["schedule_same_day_event_count"] == 1
    assert row["schedule_future_event_count"] == 1


def test_f_risk_labeling_failed_returns_clear_skip_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    histories = [_summary(i) for i in range(1, 21)]
    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: histories)
    monkeypatch.setattr(
        f_risk_generator,
        "label_notes_in_batches",
        lambda **kwargs: kwargs["audit"].update({"labeling_failed": True, "labels_usable": False}) or {},
    )

    result = f_risk_generator.generate_f_risk(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    assert result.skip_reason != "labeling_failed"
    assert result.debug_summary["risk_json"]["notes_labeling_ok"] is False


def test_f_risk_continues_when_labels_usable_even_if_merge_quality_low(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import pandas as pd

    histories = [_summary(i) for i in range(1, 21)]
    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: histories)
    monkeypatch.setattr(
        f_risk_generator,
        "label_notes_in_batches",
        lambda **kwargs: kwargs["audit"].update(
            {"labeling_failed": False, "labels_usable": True, "merge_quality_low": True, "final_coverage_rate": 1.0, "unmatched_input_dates": ["2026-03-20"]}
        ) or {h.target_date: object() for h in histories},
    )
    monkeypatch.setattr(
        f_risk_generator,
        "build_daily_feature_table",
        lambda _h, _l: pd.DataFrame(
            {
                "date": [h.target_date for h in histories],
                "expense_f_count": [1 if i % 5 == 0 else 0 for i, _ in enumerate(histories)],
                "sleep_hours": [6.5] * len(histories),
                "sleep_score": [70] * len(histories),
                "spending_total": [1000] * len(histories),
                "weather_temp_max_c": [20] * len(histories),
                "weather_precip_probability_max": [10] * len(histories),
                "task_drop_count": [0] * len(histories),
                "notes_stress_flag": [0] * len(histories),
                "is_weekend": [0] * len(histories),
            }
        ),
    )
    monkeypatch.setattr(f_risk_generator, "_fit_model", lambda *args, **kwargs: {"skipped_reason": "ml_lib_not_installed"})

    result = f_risk_generator.generate_f_risk(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")
    assert result.skip_reason != "labeling_failed"
    assert result.debug_summary["risk_json"]["notes_labeling_quality"] == "low"


def test_f_risk_case_similarity_alert_and_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import pandas as pd

    histories = [_summary(i) for i in range(1, 22)]
    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: histories)
    monkeypatch.setattr(f_risk_generator, "label_notes_in_batches", lambda **kwargs: {h.target_date: object() for h in histories})

    def _table(_h, _l):
        dates = [h.target_date for h in _h]
        n = len(dates)
        f_days = {6, 14}
        return pd.DataFrame({
            "date": dates,
            "expense_f_count": [1 if i in f_days else 0 for i in range(n)],
            "sleep_hours_lag_1": [5.5 if i in {5, 13, n - 1} else 6.8 for i in range(n)],
            "sleep_short_streak": [2 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "social_load_streak": [2 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "notes_stress_flag": [1 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "notes_stress_flag_lag_1": [1 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "notes_social_load_flag": [1 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "notes_has_drinking": [1 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "notes_has_drinking_lag_3": [1 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "late_outing_flag": [1 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "task_completion_ratio": [0.2 if i in {5, 13, n - 1} else 0.8 for i in range(n)],
            "drop_vs_7d_delta": [1.2 if i in {5, 13, n - 1} else -0.1 for i in range(n)],
            "done_vs_7d_delta": [-1.0 if i in {5, 13, n - 1} else 0.2 for i in range(n)],
            "study_minutes_lag_1": [0 if i in {5, 13, n - 1} else 120 for i in range(n)],
            "study_minutes_rolling_sum_7d": [120 if i in {5, 13, n - 1} else 700 for i in range(n)],
            "study_zero_day_streak_lag_1": [2 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "study_consistency_score_7d": [0.2 if i in {5, 13, n - 1} else 0.8 for i in range(n)],
            "weather_bad_flag": [1 if i in {5, 13, n - 1} else 0 for i in range(n)],
            "sleep_score": [65] * n,
            "spending_total": [3000] * n,
            "is_weekend": [0] * n,
        })

    monkeypatch.setattr(f_risk_generator, "build_daily_feature_table", _table)
    monkeypatch.setattr(f_risk_generator, "_fit_model", lambda *args, **kwargs: {"score": 0.4, "model": "stub", "skipped_reason": None})
    result = f_risk_generator.generate_f_risk(daily_log_read_url="read", bearer_token=None, target_date="2026-03-21")
    risk_json = result.debug_summary["risk_json"]
    assert risk_json["risk_matched"] is True
    assert risk_json["matched_case_dates"]
    assert risk_json["matched_pre_patterns"]
    assert risk_json["final_alert_basis"] in {"ml_or_similarity_or_rule_threshold", "case_similarity_high", "case_similarity_medium_plus_rules", "model_assisted_case_similarity"}


def test_f_risk_case_similarity_weak_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import pandas as pd

    histories = [_summary(i) for i in range(1, 20)]
    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: histories)
    monkeypatch.setattr(f_risk_generator, "label_notes_in_batches", lambda **kwargs: {h.target_date: object() for h in histories})
    monkeypatch.setattr(
        f_risk_generator,
        "build_daily_feature_table",
        lambda _h, _l: pd.DataFrame({
            "date": [h.target_date for h in _h],
            "expense_f_count": [1 if i in {5, 10} else 0 for i in range(len(_h))],
            "sleep_short_streak": [0] * len(_h),
            "notes_stress_flag": [0] * len(_h),
            "notes_social_load_flag": [0] * len(_h),
            "notes_has_drinking": [0] * len(_h),
            "late_outing_flag": [0] * len(_h),
            "spending_vs_7d_delta": [0] * len(_h),
            "sleep_score": [75] * len(_h),
            "spending_total": [1000] * len(_h),
            "is_weekend": [0] * len(_h),
        }),
    )
    monkeypatch.setattr(f_risk_generator, "_fit_model", lambda *args, **kwargs: {"score": 0.2, "model": "stub", "skipped_reason": None})
    result = f_risk_generator.generate_f_risk(daily_log_read_url="read", bearer_token=None, target_date="2026-03-19")
    assert result.alert_text is None
    assert result.debug_summary["risk_json"]["no_alert_reason"] in {"case_similarity_weak", "case_similarity_medium_but_rule_insufficient"}


def test_f_risk_history_days_env_and_case_type(monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import pandas as pd

    captured: dict[str, object] = {}
    histories = [_summary(i) for i in range(1, 25)]

    def _load_histories(**kwargs):
        captured["days"] = kwargs["days"]
        return histories

    monkeypatch.setenv("F_RISK_HISTORY_DAYS", "365")
    monkeypatch.setattr(f_risk_generator, "_load_histories", _load_histories)
    monkeypatch.setattr(f_risk_generator, "label_notes_in_batches", lambda **kwargs: {h.target_date: object() for h in histories})
    monkeypatch.setattr(
        f_risk_generator,
        "build_daily_feature_table",
        lambda _h, _l: pd.DataFrame({
            "date": [h.target_date for h in _h],
            "expense_f_count": [1 if i in {10, 18} else 0 for i in range(len(_h))],
            "notes_has_drinking": [1 if i in {9, 17, len(_h) - 1} else 0 for i in range(len(_h))],
            "notes_social_load_flag": [1 if i in {9, 17, len(_h) - 1} else 0 for i in range(len(_h))],
            "late_outing_flag": [1 if i in {9, 17, len(_h) - 1} else 0 for i in range(len(_h))],
            "sleep_short_streak": [2 if i in {9, 17, len(_h) - 1} else 0 for i in range(len(_h))],
            "notes_stress_flag": [1 if i in {9, 17, len(_h) - 1} else 0 for i in range(len(_h))],
            "spending_vs_7d_delta": [2800] * len(_h),
            "sleep_score": [65] * len(_h),
            "spending_total": [2000] * len(_h),
            "is_weekend": [0] * len(_h),
        }),
    )
    monkeypatch.setattr(f_risk_generator, "_fit_model", lambda *args, **kwargs: {"score": 0.35, "model": "stub", "skipped_reason": None})
    result = f_risk_generator.generate_f_risk(daily_log_read_url="read", bearer_token=None, target_date="2026-03-24")
    risk_json = result.debug_summary["risk_json"]
    assert captured["days"] == 365
    assert risk_json["history_days_loaded"] == len(histories)
    assert risk_json["matched_case_types"]
