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
        diary_input_hash=None,
        today_advice_input_hash=None,
        diary_generated_at=None,
        today_advice_generated_at=None,
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
    assert html.index("Today advice") < html.index("Sleep &amp; Condition") < html.index("Diary")


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

    assert state["today_sleep"]["comparisons"]["vs_yesterday"]["sleep_duration_min_delta"] == 30
    assert state["today_sleep"]["comparisons"]["vs_recent_7d_avg"]["sleep_score_delta"] == 5.0
    assert state["today_sleep"]["recent_3day_trend"]["sleep_duration_min"] == "up"
    assert "done_count" not in state["today_sleep"]
    assert "recent_7d_avg" in state["historical_behavior_patterns"]
    assert "notes_recording_rate_7d" in state["historical_recording_patterns"]
    assert "recent_7d_location_samples" in state["historical_context"]



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
        "location_summary",
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


def test_generation_context_excludes_today_non_sleep_fields_from_inputs() -> None:
    from scripts.mood_advice_generator import build_today_advice_generation_context

    today = _summary(
        target_date="2026-03-20",
        notes=None,
        done_count=0,
        drop_count=0,
        expenses_total=0,
        meal_summary=None,
        meal_photos=[],
        location_summary="今日はここ",
        sleep_score=70,
    )
    yesterday = _summary(
        target_date="2026-03-19",
        notes="過去メモ",
        done_count=3,
        drop_count=1,
        expenses_total=2500,
        meal_summary="定食",
        location_summary="自宅中心",
        sleep_score=80,
    )

    import scripts.mood_advice_generator as generator

    def fake_loader(**kwargs):
        return [today, yesterday]

    original = generator.load_daily_logs_for_period
    generator.load_daily_logs_for_period = fake_loader
    try:
        context = build_today_advice_generation_context(
            daily_log_read_url="read",
            bearer_token=None,
            target_date="2026-03-20",
        )
    finally:
        generator.load_daily_logs_for_period = original

    assert context is not None
    judgment_input = context["judgment_input"]
    assert "today_sleep" in judgment_input
    assert "historical_behavior_patterns" in judgment_input
    assert "historical_recording_patterns" in judgment_input
    assert "historical_context" in judgment_input
    assert "today_state" not in judgment_input
    assert "notes" not in judgment_input["today_sleep"]
    assert judgment_input["structured_historical_comparison"]["counts"]["history_days"] == 1
    assert judgment_input["top_good_days"][0]["notes"] == "過去メモ"


def test_prompt_constraints_require_today_sleep_only() -> None:
    from scripts.mood_advice_generator import FINAL_SYSTEM_PROMPT, MINI_SYSTEM_PROMPT

    for prompt in (MINI_SYSTEM_PROMPT, FINAL_SYSTEM_PROMPT):
        assert "Today advice で当日参照してよいのは sleep 系のみ" in prompt
        assert "historical data only" in prompt or "過去実績のみ" in prompt
        assert "当日の meal / done / drop / spend / notes / location summary" in prompt or "当日の done / drop / spend / meal / notes / location summary" in prompt


def test_generate_today_advice_prompt_omits_today_non_sleep_fields(monkeypatch) -> None:
    import scripts.mood_advice_generator as generator

    today = _summary(
        target_date="2026-03-20",
        notes=None,
        done_count=0,
        drop_count=0,
        expenses_total=0,
        meal_summary=None,
        meal_photos=[],
        location_summary="当日の場所",
        sleep_analysis_jp="睡眠が浅めです。",
        today_condition_forecast_jp="午前は省エネ推奨です。",
        sleep_score=68,
    )
    history = [
        today,
        _summary(target_date="2026-03-19", notes="過去メモ", done_count=4, expenses_total=2200, meal_summary="パスタ", location_summary="自宅中心"),
        _summary(target_date="2026-03-18", notes="別の過去メモ", done_count=3, expenses_total=1800, meal_summary="定食", location_summary="外出多め"),
    ]
    prompts: list[str] = []

    monkeypatch.setattr(generator, "load_daily_logs_for_period", lambda **kwargs: history)

    def fake_chat_completion(*, model: str, system_prompt: str, user_prompt: str) -> str:
        prompts.append(user_prompt)
        if len(prompts) == 1:
            return '{"day_type":"sleep","main_bottleneck":"sleep debt","priority_theme":"pace","primary_risk":"afternoon dip","good_pattern_similarity":"done avg high","bad_pattern_similarity":"notes low","notes_signal":"historical only","recording_signal":"historical only","evidence_used":["sleep score low","7日done平均"],"recommended_actions":["朝に最重要1件","午後は負荷を絞る"]}'
        return "睡眠スコアの低下を踏まえ、過去7日で着手が進んだ朝の集中帯に最重要1件を置くのが良さそうです。過去の記録ではメモや行動量の波が午後に崩れやすいため、今日は判断を増やしすぎず、午前に骨格を固めて午後は負荷を絞ってください。"

    monkeypatch.setattr(generator, "_chat_completion", fake_chat_completion)

    result = generator.generate_today_advice(
        daily_log_read_url="read",
        bearer_token=None,
        target_date="2026-03-20",
    )

    assert result is not None
    assert len(prompts) == 2
    combined_prompt = "\n".join(prompts)
    assert "当日の場所" not in combined_prompt
    assert '"sleep_score": 68' in combined_prompt
    context = generator.build_today_advice_generation_context(
        daily_log_read_url="read",
        bearer_token=None,
        target_date="2026-03-20",
    )
    assert context is not None
    today_facts = {
        "today_sleep": context["today_state"]["today_sleep"],
        "historical_behavior_patterns": context["today_state"]["historical_behavior_patterns"],
        "historical_recording_patterns": context["today_state"]["historical_recording_patterns"],
        "historical_context": context["today_state"]["historical_context"],
    }
    serialized_today_facts = str(today_facts)
    assert "done_count': 0" not in serialized_today_facts
    assert "drop_count': 0" not in serialized_today_facts
    assert "spend_total': 0" not in serialized_today_facts
    assert "meal_logged': False" not in serialized_today_facts
    assert "過去メモ" in combined_prompt


def test_count_input_tokens_returns_estimate_without_tiktoken() -> None:
    from scripts.mood_advice_generator import _build_chat_messages, _count_input_tokens

    messages = _build_chat_messages(system_prompt="sys", user_prompt="user")
    count, method = _count_input_tokens(model="gpt-4.1", messages=messages)

    assert count is not None
    assert count > 0
    assert method in {"tiktoken", "estimated_chars_div4"}
