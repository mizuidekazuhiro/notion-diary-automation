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
