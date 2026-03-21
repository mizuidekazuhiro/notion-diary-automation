from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts.daily_job import build_diary_input_fields
from scripts.sleep_condition_generator import build_sleep_insight_context


def _summary(**overrides: object) -> DailyLogSummary:
    payload = dict(
        target_date="2026-03-20",
        date="2026-03-21",
        target_date_value="2026-03-20",
        page_id="page",
        title="Daily Log｜2026-03-20",
        summary_text="",
        summary_html="",
        mail_id="run",
        source=None,
        diary=None,
        meal_summary=None,
        meal_photos=[],
        place=None,
        activity_summary=None,
        done_count=None,
        done_tasks=[],
        done_tasks_detail=[],
        drop_count=None,
        drop_tasks=[],
        kcal=None,
        protein=None,
        fat=None,
        carb=None,
        expenses_total=None,
        expenses=ExpenseSummary(total=0, count=0, top=[], remaining=0),
        location_summary=None,
        mood=None,
        notes=None,
        weight=None,
        sleep_start=None,
        sleep_end=None,
        sleep_duration_min=None,
        sleep_score=None,
        sleep_source=None,
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
        page_url=None,
        diary_notification_sent=None,
    )
    payload.update(overrides)
    return DailyLogSummary(**payload)


def main() -> None:
    today = _summary(
        sleep_start="2026-03-20T00:30:00+09:00",
        sleep_end="2026-03-20T07:10:00+09:00",
        sleep_duration_min=400,
        sleep_score=78,
        sleep_source="AutoSleep",
        sleep_heart_rate=52,
        deep_duration_min=95,
        rem_duration_min=88,
        readiness_stars=3,
        readiness_hrv=41,
        readiness_bpm=58,
        baseline_hrv=46,
        baseline_waking_bpm=51,
        sleep_analysis_jp="old analysis",
        today_condition_forecast_jp="old forecast",
    )
    history = [_summary(sleep_duration_min=360, sleep_score=70, readiness_hrv=35, readiness_bpm=60)]
    context = build_sleep_insight_context(today_summary=today, history_summaries=history)
    assert context.today_values["sleep_source"] == "AutoSleep"
    assert context.today_values["baseline_waking_bpm"] == 51
    assert context.trend_values["sleep_duration_min_delta_vs_7d"] == 40

    fields, skipped, _ = build_diary_input_fields(today)
    assert fields["Sleep Analysis JP"] == "old analysis"
    assert fields["Today Condition Forecast JP"] == "old forecast"
    assert fields["Sleep Start"].startswith("2026-03-20")
    assert "Sleep Score" in fields
    assert "Mood" in skipped

    print("OK: sleep fields are included in diary input and insight context even when previous values exist")


if __name__ == "__main__":
    main()
