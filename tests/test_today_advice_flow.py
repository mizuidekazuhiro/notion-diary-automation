from __future__ import annotations

from publish.email_templates import render_daily_log_html, render_daily_log_text
from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts.mood_advice_generator import normalize_mood_to_score


def _summary(**overrides: object) -> DailyLogSummary:
    payload = dict(
        target_date="2026-03-20",
        date="2026-03-20",
        target_date_value="2026-03-20",
        page_id="page",
        title="Daily Log｜2026-03-20",
        summary_text="🎉\n- Ship feature (Priority: High)",
        summary_html="",
        mail_id="run",
        source=None,
        diary="昨日の振り返り",
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
        expenses_total=1200,
        expenses=ExpenseSummary(total=1200, count=1, top=[], remaining=0),
        location_summary="自宅中心",
        mood="★★★★",
        notes=None,
        weight=None,
        sleep_start="2026-03-19T23:45:00+09:00",
        sleep_end="2026-03-20T07:00:00+09:00",
        sleep_duration_min=435,
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
        sleep_analysis_jp="睡眠時間はやや短めですが、深い睡眠は一定量あります。",
        today_condition_forecast_jp="午前は集中しやすい一方、午後は少し失速しやすそうです。",
        today_advice="朝の集中を使って一番重い1件を先に終わらせると、気分が安定しやすそうです。",
        page_url=None,
        diary_notification_sent=None,
    )
    payload.update(overrides)
    return DailyLogSummary(**payload)


def _payload(summary: DailyLogSummary) -> dict[str, object]:
    return {
        "target_date": summary.target_date,
        "run_id": summary.mail_id,
        "summary_text": summary.summary_text,
        "diary": summary.diary,
        "meal_summary": summary.meal_summary,
        "meal_photos": summary.meal_photos,
        "expenses_total": summary.expenses_total,
        "expenses": {"total": summary.expenses.total, "count": summary.expenses.count, "top": [], "remaining": 0},
        "location_summary": summary.location_summary,
        "mood": summary.mood,
        "weight": summary.weight,
        "today_advice": summary.today_advice,
        "sleep_analysis_jp": summary.sleep_analysis_jp,
        "today_condition_forecast_jp": summary.today_condition_forecast_jp,
        "sleep_start": summary.sleep_start,
        "sleep_end": summary.sleep_end,
        "sleep_duration_min": summary.sleep_duration_min,
    }


def test_normalize_mood_to_score_supports_star_variants() -> None:
    assert normalize_mood_to_score("★") == 1
    assert normalize_mood_to_score("★★★★") == 4
    assert normalize_mood_to_score("⭐⭐⭐⭐⭐") == 5
    assert normalize_mood_to_score("Mood 3") == 3


def test_render_daily_log_text_places_today_advice_first() -> None:
    summary = _summary()
    rendered = render_daily_log_text(_payload(summary))
    today_index = rendered.index("Today advice")
    diary_index = rendered.index("Diary")
    assert today_index < diary_index
    assert summary.today_advice in rendered


def test_render_daily_log_html_includes_today_advice_section() -> None:
    summary = _summary()
    html = render_daily_log_html(_payload(summary))
    assert "Today advice" in html
    assert summary.today_advice in html


def test_render_daily_log_text_includes_sleep_sections_in_order() -> None:
    summary = _summary()
    rendered = render_daily_log_text(_payload(summary))
    assert rendered.index("Today advice") < rendered.index("Sleep & Condition") < rendered.index("Diary")
    assert "- Sleep Analysis JP:" in rendered
    assert "- Today Condition Forecast JP:" in rendered
    assert "- 就寝時間: 23:45" in rendered
    assert "- 起床時間: 07:00" in rendered
    assert "- 睡眠時間: 7時間15分" in rendered


def test_build_today_state_includes_comparison_context() -> None:
    from scripts.mood_advice_generator import _build_today_state

    today = _summary(
        sleep_duration_min=450,
        sleep_score=82,
        readiness_hrv=48,
        readiness_bpm=52,
        done_count=3,
        drop_count=1,
    )
    recent = [
        _summary(target_date="2026-03-19", sleep_duration_min=420, sleep_score=79, readiness_hrv=45, readiness_bpm=54, done_count=2, drop_count=1),
        _summary(target_date="2026-03-18", sleep_duration_min=410, sleep_score=77, readiness_hrv=43, readiness_bpm=55, done_count=1, drop_count=2),
        _summary(target_date="2026-03-17", sleep_duration_min=400, sleep_score=75, readiness_hrv=40, readiness_bpm=56, done_count=1, drop_count=2),
    ]

    state = _build_today_state(today, recent)

    assert state["comparisons"]["vs_yesterday"]["sleep_duration_min_delta"] == 30
    assert state["comparisons"]["vs_recent_7d_avg"]["sleep_score_delta"] == 5.0
    assert state["recent_3day_trend"]["sleep_duration_min"] == "up"
    assert state["recent_3day_trend"]["drop_count"] is None



def test_build_structured_comparison_uses_last_30_days_and_top_samples() -> None:
    from scripts.mood_advice_generator import _build_structured_comparison

    history = []
    for day in range(30):
        mood = "★★★★★" if day < 5 else "★" if 5 <= day < 10 else "★★★"
        history.append(
            _summary(
                target_date=f"2026-03-{30 - day:02d}",
                mood=mood,
                diary=f"diary-{day}",
                notes=f"note-{day}" if day % 2 == 0 else None,
                meal_summary=f"meal-{day}",
                sleep_score=90 - day,
                sleep_duration_min=420 + day,
                done_count=5 - (day % 3),
                drop_count=day % 2,
                expenses_total=1000 + day * 10,
            )
        )

    structured = _build_structured_comparison(history)

    assert structured["counts"]["last_30_days_count"] == 30
    assert structured["counts"]["top_good_days_count"] == 5
    assert structured["counts"]["top_bad_days_count"] == 5
    assert structured["counts"]["diary_used"] is False
    assert len(structured["last_30_days_summary"]["daily_records"]) == 30
    assert len(structured["top_good_days"]) == 5
    assert len(structured["top_bad_days"]) == 5
    assert set(structured["top_good_days"][0].keys()) == {
        "date",
        "sleep_start",
        "sleep_end",
        "sleep_duration_min",
        "sleep_score",
        "meal_summary",
        "meal_logged",
        "done_count",
        "drop_count",
        "spend_total",
        "notes",
        "daily_score",
    }
    assert "diary" not in structured["top_good_days"][0]
    assert "diary" not in structured["last_30_days_summary"]["daily_records"][0]


def test_count_input_tokens_returns_estimate_without_tiktoken() -> None:
    from scripts.mood_advice_generator import _build_chat_messages, _count_input_tokens

    messages = _build_chat_messages(system_prompt="sys", user_prompt="user")
    count, method = _count_input_tokens(model="gpt-4.1", messages=messages)

    assert count is not None
    assert count > 0
    assert method in {"tiktoken", "estimated_chars_div4"}
