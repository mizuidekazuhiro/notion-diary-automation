from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts import daily_job
from scripts.location_for_weather import ResolvedLocation
from scripts.mood_advice_generator import MoodAdviceResult
from scripts.sleep_condition_generator import build_sleep_insight_context, generate_sleep_insights


class _DummyResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self._content}}]}


def _summary(**overrides: object) -> DailyLogSummary:
    payload = dict(
        target_date="2026-03-20",
        date="2026-03-20",
        target_date_value="2026-03-20",
        page_id="page",
        title="Daily Log｜2026-03-20",
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
        notes="午前は眠気あり",
        weight=None,
        sleep_start="2026-03-19T23:30:00+09:00",
        sleep_end="2026-03-20T07:00:00+09:00",
        sleep_duration_min=450,
        resolved_sleep_duration_min=450,
        resolved_sleep_duration_hours=7.5,
        resolved_sleep_duration_text="7時間30分",
        sleep_duration_source="derived_from_start_end",
        sleep_score=80,
        sleep_source="AutoSleep",
        readiness_stars=3,
        readiness_hrv=45,
        readiness_bpm=55,
        baseline_hrv=48,
        baseline_waking_bpm=52,
        sleep_heart_rate=51,
        deep_duration_min=95,
        rem_duration_min=105,
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


class _Config(SimpleNamespace):
    pass


def test_sleep_generator_returns_only_sleep_fields(monkeypatch) -> None:
    context = build_sleep_insight_context(
        today_summary=_summary(),
        history_summaries=[_summary(target_date="2026-03-19", sleep_duration_min=420, sleep_score=76)],
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_post(*args, **kwargs):
        return _DummyResponse('{"sleep_analysis_jp":"analysis","today_condition_forecast_jp":"forecast","today_advice":"must be ignored"}')

    monkeypatch.setattr("scripts.sleep_condition_generator.requests.post", fake_post)
    result = generate_sleep_insights(target_date="2026-03-20", context=context)

    assert result == {
        "sleep_analysis_jp": "analysis",
        "today_condition_forecast_jp": "forecast",
    }
    assert "today_advice" not in result


def test_mood_advice_result_does_not_expose_sleep_fields() -> None:
    field_names = {field.name for field in fields(MoodAdviceResult)}
    assert "today_advice" in field_names
    assert "sleep_analysis_jp" not in field_names
    assert "today_condition_forecast_jp" not in field_names


def test_phase_c_runs_sleep_then_advice_then_diary(monkeypatch) -> None:
    order: list[str] = []
    summary = _summary()
    refreshed = {"current": summary}
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: refreshed["current"])

    def fake_sleep(config, *, summary, run_id):
        order.append("sleep")
        return summary

    def fake_advice(config, *, summary, run_id):
        order.append("advice")
        return summary

    def fake_diary(config, *, summary, run_id, **kwargs):
        order.append("diary")
        refreshed["current"] = _summary(diary="generated diary")
        return refreshed["current"]

    monkeypatch.setattr(daily_job, "_generate_and_save_weather", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_compute_expense_f_alert", lambda *, summary, run_id: {"matched": False, "reasons": []})
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", fake_sleep)
    monkeypatch.setattr(daily_job, "_generate_and_save_f_risk", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", fake_advice)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", fake_diary)
    daily_job.run_notify_diary(config, "2026-03-20", "run")
    assert order == ["sleep", "advice", "diary"]


def test_email_disabled_does_not_mark_notified(monkeypatch) -> None:
    summary = _summary(diary="generated diary", diary_notification_sent=None)
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")
    mark_calls: list[dict[str, object]] = []

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_weather", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_f_risk", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_notify_phase_c", lambda config, *, summary, run_id: False)
    monkeypatch.setattr(daily_job, "post_json", lambda *args, **kwargs: mark_calls.append(kwargs) or {"updated": True})

    daily_job.run_notify_diary(config, "2026-03-20", "run")

    assert mark_calls == []


def test_email_disabled_still_runs_f_risk(monkeypatch) -> None:
    order: list[str] = []
    summary = _summary(diary="generated diary", diary_notification_sent=None)
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_weather", lambda config, *, summary, run_id: order.append("weather") or summary)
    monkeypatch.setattr(daily_job, "_compute_expense_f_alert", lambda *, summary, run_id: order.append("expense_f") or {"matched": False, "reasons": []})
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", lambda config, *, summary, run_id: order.append("sleep") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_f_risk", lambda config, *, summary, run_id: order.append("f_risk") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", lambda config, *, summary, run_id: order.append("advice") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", lambda config, *, summary, run_id, **kwargs: order.append("diary") or summary)
    daily_job.run_notify_diary(config, "2026-03-20", "run")
    assert order == ["weather", "expense_f", "sleep", "f_risk", "advice", "diary"]


def test_already_notified_still_runs_generation(monkeypatch) -> None:
    order: list[str] = []
    summary = _summary(diary="generated diary", diary_notification_sent=True)
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", lambda config, *, summary, run_id: order.append("sleep") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", lambda config, *, summary, run_id: order.append("advice") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", lambda config, *, summary, run_id, **kwargs: order.append("diary") or summary)

    daily_job.run_notify_diary(config, "2026-03-20", "run")
    assert order == ["sleep", "advice", "diary"]


def test_diary_existing_with_same_hash_skips(monkeypatch) -> None:
    summary = _summary(diary="existing diary")
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []
    diary_input_fields, _, _, _ = daily_job.build_diary_input_fields(summary)
    hash_payload, _ = daily_job._build_diary_hash_payload(summary, diary_input_fields)
    current_hash, _, _ = daily_job._build_input_hash(hash_payload)
    summary = _summary(diary="existing diary", diary_input_hash=current_hash)

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "generate_diary_from_daily_log", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should skip")))
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: save_calls.append(kwargs) or {"updated": True, "reason": "updated"})

    result = daily_job._generate_and_save_diary(config, summary=summary, run_id="run")

    assert result == summary
    assert save_calls == []


def test_diary_existing_with_changed_hash_regenerates(monkeypatch) -> None:
    summary = _summary(diary="existing diary", notes="new notes", diary_input_hash="old-hash")
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "generate_diary_from_daily_log", lambda *args, **kwargs: "regenerated diary")
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: save_calls.append(kwargs) or {"updated": True, "reason": "updated"})

    daily_job._generate_and_save_diary(config, summary=summary, run_id="run")

    assert len(save_calls) == 1
    payload = save_calls[0]["payload"]
    assert payload["diary"] == "regenerated diary"
    assert payload["diary_input_hash"] != "old-hash"
    assert payload["diary_generated_at"].endswith("Z")


def test_weather_roundtrip_compare_ignores_unfetched_fields() -> None:
    summary = _summary(
        weather_summary="晴れ。最高25.0℃、最低15.0℃、降水量0.0mmです。",
        weather_location="東京",
        weather_retrieved_at="2026-03-20T00:00:00Z",
        weather_generated_at="2026-03-20T00:01:00Z",
        weather_input_hash="abc",
        weather_temp_max_c=25.0,
        weather_temp_min_c=15.0,
        weather_precip_probability_max=50.0,  # stale value in Notion
        weather_code=0,
    )
    expected_payload = {
        "weather": "晴れ。最高25.0℃、最低15.0℃、降水量0.0mmです。",
        "weather_summary": "晴れ。最高25.0℃、最低15.0℃、降水量0.0mmです。",
        "weather_location": "東京",
        "weather_temp_max_c": 25.0,
        "weather_temp_min_c": 15.0,
        "weather_precip_probability_max": None,
        "weather_code": 0,
        "weather_retrieved_at": "2026-03-20T00:00:00Z",
        "weather_input_hash": "abc",
        "weather_generated_at": "2026-03-20T00:01:00Z",
    }
    status = daily_job._weather_roundtrip_status(summary=summary, expected_payload=expected_payload)
    assert status["compare_ok"] is True
    assert "weather_precip_probability_max" not in status["mismatch_fields"]
    assert "weather_precip_probability_max" in status["ignored_fields"]


def test_today_advice_existing_with_same_hash_skips(monkeypatch) -> None:
    summary = _summary(today_advice="existing advice")
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []

    fake_context = {
        "today_state": {
            "today_sleep": {"sleep_duration_min": 450},
            "historical_behavior_patterns": {"recent_7d_avg": {"done_count_avg": 2}},
            "historical_recording_patterns": {"notes_recording_rate_7d": 0.6},
            "historical_context": {"recent_7d_location_samples": ["自宅中心"]},
        },
        "structured": {"counts": {"last_30_days_count": 3}, "high_mood_sample_count": 1, "low_mood_sample_count": 1},
        "judgment_input": {"today_sleep": {"a": 1}, "structured_historical_comparison": {"b": 2}, "top_good_days": [], "top_bad_days": [], "input_policy": {"diary_used": False}},
    }
    current_hash, _, _ = daily_job._build_input_hash(
        {
            "judgment_input": fake_context["judgment_input"],
            "today_facts": {
                "today_sleep": fake_context["today_state"]["today_sleep"],
                "historical_behavior_patterns": fake_context["today_state"]["historical_behavior_patterns"],
                "historical_recording_patterns": fake_context["today_state"]["historical_recording_patterns"],
                "historical_context": fake_context["today_state"]["historical_context"],
            },
        }
    )
    summary = _summary(today_advice="existing advice", today_advice_input_hash=current_hash)

    monkeypatch.setattr(daily_job, "build_today_advice_generation_context", lambda **kwargs: fake_context)
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "generate_today_advice", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should skip")))
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: save_calls.append(kwargs) or {"updated": True, "reason": "updated"})

    result = daily_job._generate_and_save_today_advice(config, summary=summary, run_id="run")

    assert result == summary
    assert save_calls == []


def test_resolve_target_date_defaults_to_yesterday_and_supports_today_mode(monkeypatch) -> None:
    now = datetime(2026, 3, 22, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo"))

    monkeypatch.delenv("TODAY_ADVICE_TARGET_MODE", raising=False)
    assert daily_job.resolve_target_date(explicit_target_date=None, now=now, phase="notify_diary") == "2026-03-21"

    monkeypatch.setenv("TODAY_ADVICE_TARGET_MODE", "TODAY")
    assert daily_job.resolve_target_date(explicit_target_date=None, now=now, phase="notify_diary") == "2026-03-22"

    assert daily_job.resolve_target_date(explicit_target_date="2026-03-10", now=now, phase="notify_diary") == "2026-03-10"
    assert daily_job.resolve_target_date(explicit_target_date=None, now=now, phase="publish") == "2026-03-21"


def test_get_today_advice_target_mode_rejects_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("TODAY_ADVICE_TARGET_MODE", "INVALID")

    try:
        daily_job.get_today_advice_target_mode()
    except RuntimeError as exc:
        assert "YESTERDAY or TODAY" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_today_advice_existing_with_changed_hash_regenerates(monkeypatch) -> None:
    summary = _summary(today_advice="existing advice", today_advice_input_hash="old-hash")
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []
    fake_context = {
        "today_state": {
            "today_sleep": {"sleep_duration_min": 450},
            "historical_behavior_patterns": {"recent_7d_avg": {"done_count_avg": 2}},
            "historical_recording_patterns": {"notes_recording_rate_7d": 0.6},
            "historical_context": {"recent_7d_location_samples": ["自宅中心"]},
        },
        "structured": {"counts": {"last_30_days_count": 3}, "high_mood_sample_count": 1, "low_mood_sample_count": 1},
        "judgment_input": {"today_sleep": {"a": 1}, "structured_historical_comparison": {"b": 2}, "top_good_days": [], "top_bad_days": [], "input_policy": {"diary_used": False}},
    }
    advice_result = MoodAdviceResult(
        today_advice="regenerated advice",
        judgment_json={"evidence_used": []},
        judgment_text="{}",
        high_mood_sample_count=1,
        low_mood_sample_count=1,
        history_count=3,
    )

    monkeypatch.setattr(daily_job, "build_today_advice_generation_context", lambda **kwargs: fake_context)
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "generate_today_advice", lambda **kwargs: advice_result)
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: save_calls.append(kwargs) or {"updated": True, "reason": "updated"})

    daily_job._generate_and_save_today_advice(config, summary=summary, run_id="run")


def test_f_risk_no_f_history_skips_save(monkeypatch) -> None:
    from scripts.f_risk_generator import FRiskResult

    summary = _summary()
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        daily_job,
        "generate_f_risk",
        lambda **kwargs: FRiskResult(
            alert_text=None,
            score=None,
            reason=None,
            matched_patterns=[],
            skip_reason="no_f_history",
            debug_summary={"train_rows": 20},
        ),
    )
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: save_calls.append(kwargs) or {"updated": True})
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)

    result = daily_job._generate_and_save_f_risk(config, summary=summary, run_id="run")

    assert result == summary
    assert save_calls == []


def test_weather_missing_location_log_updates_empty_weather(monkeypatch) -> None:
    summary = _summary()
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        daily_job,
        "resolve_location_for_weather",
        lambda **kwargs: ResolvedLocation(
            name=None,
            source="location_log_db",
            skip_reason="missing_notion_env",
            debug_summary={},
        ),
    )
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: save_calls.append(kwargs) or {"updated": True})
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)

    daily_job._generate_and_save_weather(config, summary=summary, run_id="run")

    assert len(save_calls) == 1
    assert save_calls[0]["payload"]["weather"] == ""


def test_weather_save_payload_contains_required_and_detail_fields(monkeypatch) -> None:
    summary = _summary()
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []
    weather_fetch_calls: list[dict[str, object]] = []
    refreshed = _summary(
        weather_summary="晴れ",
        weather_location="東京",
        weather_temp_max_c=23.0,
        weather_temp_min_c=15.0,
        weather_precip_probability_max=10.0,
        weather_code=0,
        weather_retrieved_at="2026-03-20T00:00:00Z",
        weather_input_hash="h",
        weather_generated_at="2026-03-20T00:01:00Z",
    )
    monkeypatch.setattr(
        daily_job,
        "resolve_location_for_weather",
        lambda **kwargs: ResolvedLocation(
            name="東京",
            source="location_log_db",
            skip_reason=None,
            latitude=35.0,
            longitude=139.0,
            resolution_method="location_log_latest_latlon",
            debug_summary={"query_status": "ok"},
        ),
    )
    monkeypatch.setattr(
        daily_job,
        "fetch_weather_for_date",
        lambda **kwargs: weather_fetch_calls.append(kwargs)
        or SimpleNamespace(
            available=True,
            location_label="東京",
            summary="晴れ",
            temp_max_c=23.0,
            temp_min_c=15.0,
            precip_probability_max=None,
            precipitation_sum_mm=0.0,
            weather_code=0,
            retrieved_at="2026-03-20T00:00:00Z",
            debug_summary={},
        ),
    )

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            del tz
            return cls(2026, 3, 29, 15, 35, tzinfo=ZoneInfo("Asia/Tokyo"))

    monkeypatch.setattr(daily_job, "datetime", _FrozenDatetime)
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: save_calls.append(kwargs) or {"updated": True, "reason": "updated"})
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: refreshed)

    daily_job._generate_and_save_weather(config, summary=summary, run_id="run")

    assert weather_fetch_calls[0]["target_date"] == "2026-03-29"
    assert save_calls[0]["target_date"] == "2026-03-20"
    payload = save_calls[0]["payload"]
    assert payload["weather"] == "晴れ"
    assert payload["weather_summary"] == "晴れ"
    assert payload["weather_location"] == "東京"
    assert payload["weather_temp_max_c"] == 23.0
    assert payload["weather_temp_min_c"] == 15.0
    assert payload["weather_precip_probability_max"] is None
    assert payload["weather_code"] == 0


def test_weather_input_hash_changes_when_forecast_date_changes() -> None:
    base_payload = {
        "daily_log_target_date": "2026-03-28",
        "weather_forecast_date_jst": "2026-03-29",
        "location_name": "東京",
        "location_latitude": 35.0,
        "location_longitude": 139.0,
        "location_resolution_method": "location_log_latest_latlon",
        "location_source": "location_log_db",
        "weather_provider": "open-meteo-jma",
    }
    next_payload = {**base_payload, "weather_provider": "legacy-provider"}

    first_hash, _, _ = daily_job._build_input_hash(base_payload)
    second_hash, _, _ = daily_job._build_input_hash(next_payload)

    assert first_hash != second_hash


def test_weather_roundtrip_datetime_normalization_accepts_z_and_utc_offset() -> None:
    expected_payload = {
        "weather": "晴れ",
        "weather_summary": "晴れ",
        "weather_retrieved_at": "2026-03-29T02:54:47Z",
        "weather_generated_at": "2026-03-29T02:54:47Z",
        "weather_input_hash": "hash-1",
    }
    summary = _summary(
        weather_summary="晴れ",
        weather_retrieved_at="2026-03-29T02:54:47+00:00",
        weather_generated_at="2026-03-29T02:54:47+00:00",
        weather_input_hash="hash-1",
    )

    status = daily_job._weather_roundtrip_status(summary=summary, expected_payload=expected_payload)

    assert status["readback_ok"] is True
    assert status["compare_ok"] is True
    assert status["missing_fields"] == []
    assert status["mismatch_fields"] == []


def test_weather_roundtrip_text_and_number_fields_match_end_to_end() -> None:
    expected_payload = {
        "weather": "晴れ",
        "weather_summary": "晴れ",
        "weather_location": "東京",
        "weather_temp_max_c": 23.0,
        "weather_temp_min_c": 15.0,
        "weather_precip_probability_max": None,
        "weather_code": 0,
        "weather_retrieved_at": "2026-03-20T00:00:00Z",
        "weather_input_hash": "hash-1",
        "weather_generated_at": "2026-03-20T00:01:00Z",
    }
    summary = _summary(
        weather_summary="晴れ",
        weather_location="東京",
        weather_temp_max_c=23.0,
        weather_temp_min_c=15.0,
        weather_precip_probability_max=None,
        weather_code=0,
        weather_retrieved_at="2026-03-20T00:00:00+00:00",
        weather_input_hash="hash-1",
        weather_generated_at="2026-03-20T00:01:00+00:00",
    )

    status = daily_job._weather_roundtrip_status(summary=summary, expected_payload=expected_payload)

    assert status["readback_ok"] is True
    assert status["compare_ok"] is True
    assert status["missing_fields"] == []
    assert status["mismatch_fields"] == []


def test_workers_daily_log_read_weather_resolution_uses_exact_names_and_value_fallback() -> None:
    source = Path("workers/src/index.ts").read_text(encoding="utf-8")

    assert "resolveExactPropertyName(\n    properties,\n    getWeatherPropertyName(env),\n    \"daily_log_read:weather\"," in source
    assert "resolveExactPropertyName(\n    properties,\n    getWeatherSummaryPropertyName(env),\n    \"daily_log_read:weather_summary\"," in source
    assert "[\"Weather Summary\", \"weather\", \"weather_summary\"]" not in source
    assert "[\"Weather\", \"weather\", \"weather_summary\"]" not in source
    assert "? getStringFromProperty(properties[weatherPropertyName])" in source
    assert "? getStringFromProperty(properties[weatherSummaryPropertyName])" in source
    assert "const weatherSummary = (weatherSummaryResolvedText || weatherResolvedText || null);" in source
    assert "const weatherLegacyText = (weatherResolvedText || weatherSummaryResolvedText || null);" in source
    assert "function getIsoStringFromProperty(property: Record<string, any> | undefined): string {" in source
    assert "? getIsoStringFromProperty(properties[weatherRetrievedAtPropertyName])" in source
    assert "? getIsoStringFromProperty(properties[weatherGeneratedAtPropertyName])" in source




def test_workers_daily_log_read_includes_study_fields_in_response() -> None:
    source = Path("workers/src/index.ts").read_text(encoding="utf-8")

    assert """resolvePropertyName(
    properties,
    \"Study Minutes\",
    \"daily_log_read:study_minutes\",
    [\"study_minutes\"],""" in source
    assert """resolvePropertyName(
    properties,
    \"Study Sessions\",
    \"daily_log_read:study_sessions\",
    [\"study_sessions\"],""" in source
    assert """resolvePropertyName(
    properties,
    \"Study Last Used At\",
    \"daily_log_read:study_last_used_at\",
    [\"study_last_used_at\"],""" in source
    assert "study_minutes: studyMinutes," in source
    assert "study_sessions: studySessions," in source
    assert "study_last_used_at: studyLastUsedAt," in source


def test_workers_daily_log_read_study_last_used_at_null_fallback_is_explicit() -> None:
    source = Path("workers/src/index.ts").read_text(encoding="utf-8")

    assert "getDateTimeFromProperty(properties[studyLastUsedAtPropertyName]) ||" in source
    assert "getStringFromProperty(properties[studyLastUsedAtPropertyName]) ||" in source
    assert "null\n    : null;" in source


def test_workers_generate_diary_weather_summary_and_select_are_separated() -> None:
    source = Path("workers/src/index.ts").read_text(encoding="utf-8")
    assert "const weatherSummaryTextResolved =" in source
    assert "const weatherSelectLabel = inferWeatherSelectLabel(weatherCode, weatherSummaryTextResolved);" in source
    assert 'weatherSelectSkipReason = "select_option_not_found";' in source
    assert "updated_with_weather_select_skip" in source


def test_build_input_hash_normalizes_empty_values() -> None:
    payload_a = {
        "notes": " hello ",
        "items": ["a", "", "  "],
        "nested": {"x": "", "y": None, "z": 1.0},
    }
    payload_b = {
        "notes": "hello",
        "items": ["a"],
        "nested": {"z": 1},
    }

    hash_a, normalized_a, _ = daily_job._build_input_hash(payload_a)
    hash_b, normalized_b, _ = daily_job._build_input_hash(payload_b)

    assert hash_a == hash_b
    assert normalized_a == normalized_b == {
        "items": ["a"],
        "nested": {"z": 1},
        "notes": "hello",
    }


def test_workflow_run_names_match_dependencies() -> None:
    workflow_dir = Path(".github/workflows")
    ingest = (workflow_dir / "ingest_daily_log.yml").read_text(encoding="utf-8")
    location = (workflow_dir / "location_summary.yml").read_text(encoding="utf-8")
    diary = (workflow_dir / "diary_notify.yml").read_text(encoding="utf-8")
    publish = (workflow_dir / "publish_daily_mail.yml").read_text(encoding="utf-8")

    assert "name: Daily Diary 01 - Ingest Daily Log" in ingest
    assert "- Daily Diary 01 - Ingest Daily Log" in location
    assert "name: Daily Diary 02 - Generate Location Summary" in location
    assert "- Daily Diary 02 - Generate Location Summary" in diary
    assert "name: Daily Diary 03 - Generate Diary & Sleep Insights" in diary
    assert "- Daily Diary 03 - Generate Diary & Sleep Insights" in publish


def test_notify_diary_does_not_call_mail_notification_renderer_or_sender(monkeypatch) -> None:
    summary = _summary(diary="generated diary")
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_weather", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_f_risk", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", lambda config, *, summary, run_id, **kwargs: summary)
    monkeypatch.setattr(daily_job, "render_diary_notification_mail", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not render notification mail")))
    monkeypatch.setattr(daily_job, "send_mail", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not send mail in notify_diary")))
    daily_job.run_notify_diary(config, "2026-03-20", "run")


def test_load_config_notify_diary_does_not_require_mail_env(monkeypatch) -> None:
    monkeypatch.setattr(daily_job, "parse_args", lambda: SimpleNamespace(phase="notify_diary", target_date="2026-03-20"))
    monkeypatch.setattr(daily_job, "run_notify_diary", lambda config, target_date, run_id: None)
    monkeypatch.delenv("MAIL_FROM", raising=False)
    monkeypatch.delenv("MAIL_TO", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setenv("DAILY_LOG_UPSERT_URL", "https://example.com/api/daily_log/upsert")
    monkeypatch.delenv("TASKS_CLOSED_URL", raising=False)
    daily_job.main()
