from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts import daily_job
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

    def fake_diary(config, *, summary, run_id):
        order.append("diary")
        refreshed["current"] = _summary(diary="generated diary")
        return refreshed["current"]

    def fake_notify(config, *, summary, run_id):
        order.append("notify")
        return False

    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", fake_sleep)
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", fake_advice)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", fake_diary)
    monkeypatch.setattr(daily_job, "_notify_phase_c", fake_notify)

    daily_job.run_notify_diary(config, "2026-03-20", "run")

    assert order == ["sleep", "advice", "diary", "notify"]


def test_email_disabled_does_not_mark_notified(monkeypatch) -> None:
    summary = _summary(diary="generated diary", diary_notification_sent=None)
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")
    mark_calls: list[dict[str, object]] = []

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", lambda config, *, summary, run_id: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", lambda config, *, summary, run_id: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", lambda config, *, summary, run_id: summary)
    monkeypatch.setattr(daily_job, "_notify_phase_c", lambda config, *, summary, run_id: False)
    monkeypatch.setattr(daily_job, "post_json", lambda *args, **kwargs: mark_calls.append(kwargs) or {"updated": True})

    daily_job.run_notify_diary(config, "2026-03-20", "run")

    assert mark_calls == []


def test_already_notified_skips_only_notify(monkeypatch) -> None:
    order: list[str] = []
    summary = _summary(diary="generated diary", diary_notification_sent=True)
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen", diary_mark_notified_url="mark")

    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda config, target_date: summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_sleep_insights", lambda config, *, summary, run_id: order.append("sleep") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_today_advice", lambda config, *, summary, run_id: order.append("advice") or summary)
    monkeypatch.setattr(daily_job, "_generate_and_save_diary", lambda config, *, summary, run_id: order.append("diary") or summary)

    def fake_notify(config, *, summary, run_id):
        order.append("notify")
        return False

    monkeypatch.setattr(daily_job, "_notify_phase_c", fake_notify)

    daily_job.run_notify_diary(config, "2026-03-20", "run")

    assert order == ["sleep", "advice", "diary", "notify"]


def test_diary_existing_with_same_hash_skips(monkeypatch) -> None:
    summary = _summary(diary="existing diary")
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []
    diary_input_fields, _, _ = daily_job.build_diary_input_fields(summary)
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


def test_today_advice_existing_with_same_hash_skips(monkeypatch) -> None:
    summary = _summary(today_advice="existing advice")
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []

    fake_context = {
        "today_state": {
            "today_sleep": {"sleep_duration_min": 450},
            "today_activity_context": {"notes": "午前は眠気あり"},
            "comparisons": {"vs_yesterday": {"sleep_duration_min_delta": 20}},
            "recent_3day_trend": {"sleep_duration_min": "up"},
        },
        "structured": {"counts": {"last_30_days_count": 3}, "high_mood_sample_count": 1, "low_mood_sample_count": 1},
        "judgment_input": {"today_state": {"a": 1}, "structured_comparison": {"b": 2}, "top_good_days": [], "top_bad_days": [], "input_policy": {"diary_used": False}},
    }
    current_hash, _, _ = daily_job._build_input_hash(
        {
            "judgment_input": fake_context["judgment_input"],
            "today_facts": {
                "today_sleep": fake_context["today_state"]["today_sleep"],
                "today_activity_context": fake_context["today_state"]["today_activity_context"],
                "comparisons": fake_context["today_state"]["comparisons"],
                "recent_3day_trend": fake_context["today_state"]["recent_3day_trend"],
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


def test_today_advice_existing_with_changed_hash_regenerates(monkeypatch) -> None:
    summary = _summary(today_advice="existing advice", today_advice_input_hash="old-hash")
    config = _Config(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    save_calls: list[dict[str, object]] = []
    fake_context = {
        "today_state": {
            "today_sleep": {"sleep_duration_min": 450},
            "today_activity_context": {"notes": "午前は眠気あり"},
            "comparisons": {"vs_yesterday": {"sleep_duration_min_delta": 20}},
            "recent_3day_trend": {"sleep_duration_min": "up"},
        },
        "structured": {"counts": {"last_30_days_count": 3}, "high_mood_sample_count": 1, "low_mood_sample_count": 1},
        "judgment_input": {"today_state": {"a": 1}, "structured_comparison": {"b": 2}, "top_good_days": [], "top_bad_days": [], "input_policy": {"diary_used": False}},
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

    assert len(save_calls) == 1
    payload = save_calls[0]["payload"]
    assert payload["today_advice"] == "regenerated advice"
    assert payload["today_advice_input_hash"] != "old-hash"
    assert payload["today_advice_generated_at"].endswith("Z")


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
