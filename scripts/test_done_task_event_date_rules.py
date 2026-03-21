from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publish.read_daily_log import DailyLogSummary, DoneTaskDetail, ExpenseSummary
from scripts.daily_job import _build_done_tasks_detail_text, build_diary_input_fields
from scripts.diary_generator import _build_prompts


def _dummy_summary() -> DailyLogSummary:
    return DailyLogSummary(
        target_date="2026-02-02",
        date="2026-02-03",
        target_date_value="2026-02-02",
        page_id="dummy",
        title="Daily Log｜2026-02-02",
        summary_text="",
        summary_html="",
        mail_id="",
        source=None,
        diary=None,
        meal_summary=None,
        meal_photos=[],
        place=None,
        activity_summary=None,
        done_count=3,
        done_tasks=["会食予定登録", "打合せ", "通常タスク"],
        done_tasks_detail=[
            DoneTaskDetail(
                title="会食予定登録",
                done_date="2026-02-02",
                event_date="2026-02-26T20:00:00+09:00",
            ),
            DoneTaskDetail(
                title="打合せ",
                done_date="2026-02-02",
                event_date="2026-02-02T10:00:00+09:00",
            ),
            DoneTaskDetail(title="通常タスク", done_date="2026-02-02", event_date=None),
        ],
        drop_count=0,
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


def main() -> None:
    summary = _dummy_summary()

    detail_text = _build_done_tasks_detail_text(summary)
    assert "会食予定登録 | done_date=2026-02-02 | event_date=2026-02-26T20:00:00+09:00" in detail_text
    assert "打合せ | done_date=2026-02-02 | event_date=2026-02-02T10:00:00+09:00" in detail_text
    assert "通常タスク | done_date=2026-02-02 | event_date=null" in detail_text

    fields, skipped, _ = build_diary_input_fields(summary)
    assert "Done Tasks Detail" in fields
    assert "Drop Tasks" in skipped

    system_prompt, user_prompt = _build_prompts(fields, summary.target_date)
    assert "event_date が target_date より未来なら、その日にイベントがあったとは絶対に書かない" in system_prompt
    assert "event_date が target_date と一致する場合のみ" in system_prompt
    assert "event_date が空の Done task は通常の完了タスク" in system_prompt
    assert "Done Tasks Detail:" in user_prompt

    print("OK: done task event_date rules are embedded in diary inputs and prompts")


if __name__ == "__main__":
    main()
