from __future__ import annotations

import json
import importlib.util

import pytest
from publish.email_templates import render_daily_log_html, render_daily_log_text
from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts.mood_advice_generator import normalize_mood_to_score

HAS_PANDAS = importlib.util.find_spec("pandas") is not None


def _patch_history_result(
    monkeypatch: pytest.MonkeyPatch,
    generator_module,
    summaries: list[DailyLogSummary],
    *,
    include_next_day: bool = False,
    failed_dates: list[str] | None = None,
    missing_dates: list[str] | None = None,
) -> None:
    failed_dates = failed_dates or []
    missing_dates = missing_dates or []
    requested_dates = [item.target_date for item in summaries] + failed_dates + missing_dates

    def _fake_loader(**kwargs):
        return generator_module.TodayAdviceHistoryLoadResult(
            summaries=summaries,
            requested_dates=requested_dates,
            failed_dates=failed_dates,
            missing_dates=missing_dates,
            include_next_day=include_next_day,
        )

    monkeypatch.setattr(generator_module, "_load_daily_logs_for_period_with_debug", _fake_loader)


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
        resolved_sleep_duration_min=435,
        resolved_sleep_duration_hours=7.25,
        resolved_sleep_duration_text="7時間15分",
        sleep_duration_source="derived_from_start_end",
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
    if "resolved_sleep_duration_min" not in overrides:
        payload["resolved_sleep_duration_min"] = payload.get("sleep_duration_min")
    if "resolved_sleep_duration_hours" not in overrides:
        minutes = payload.get("resolved_sleep_duration_min")
        payload["resolved_sleep_duration_hours"] = (round(float(minutes) / 60.0, 2) if isinstance(minutes, (int, float)) else None)
    if "resolved_sleep_duration_text" not in overrides:
        payload["resolved_sleep_duration_text"] = None
    if "sleep_duration_source" not in overrides:
        payload["sleep_duration_source"] = "derived_from_start_end"
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
    assert "Diary" in html and "Sleep &amp; Condition" in html


def test_render_daily_log_text_includes_sleep_sections_in_order() -> None:
    summary = _summary()
    rendered = render_daily_log_text(_payload(summary))
    assert rendered.index("Today advice") < rendered.index("Diary") < rendered.index("Sleep & Condition")
    assert "- Sleep Analysis JP:" in rendered
    assert "- Today Condition Forecast JP:" in rendered
    assert "- 就寝時間: 23:45" in rendered
    assert "- 起床時間: 07:00" in rendered
    assert "- 睡眠時間: 7時間15分" in rendered


def test_build_today_state_includes_comparison_context() -> None:
    from scripts.mood_advice_generator import _build_today_state

    today = _summary(
        sleep_start=None,
        sleep_end=None,
        sleep_duration_min=450,
        sleep_score=82,
        readiness_hrv=48,
        readiness_bpm=52,
        done_count=3,
        drop_count=1,
    )
    recent = [
        _summary(target_date="2026-03-19", sleep_start=None, sleep_end=None, sleep_duration_min=420, sleep_score=79, readiness_hrv=45, readiness_bpm=54, done_count=2, drop_count=1),
        _summary(target_date="2026-03-18", sleep_start=None, sleep_end=None, sleep_duration_min=410, sleep_score=77, readiness_hrv=43, readiness_bpm=55, done_count=1, drop_count=2),
        _summary(target_date="2026-03-17", sleep_start=None, sleep_end=None, sleep_duration_min=400, sleep_score=75, readiness_hrv=40, readiness_bpm=56, done_count=1, drop_count=2),
    ]

    state = _build_today_state(today, recent)

    assert state["today_sleep"]["comparisons"]["vs_yesterday"]["sleep_duration_min_delta"] == 30
    assert state["today_sleep"]["comparisons"]["vs_recent_7d_avg"]["sleep_score_delta"] == 5.0
    assert state["today_sleep"]["recent_3day_trend"]["sleep_duration_min"] == "up"
    assert "done_count" not in state["today_sleep"]
    assert "recent_7d_avg" in state["historical_behavior_patterns"]
    assert "recent_7d_vs_30d" in state["historical_behavior_patterns"]
    assert "notes_recording_rate_7d" in state["historical_recording_patterns"]
    assert "location_recording_rate_7d" in state["historical_recording_patterns"]
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
                kcal=1800 + day * 10,
                protein=90 + day,
                fat=50 + day,
                carb=200 + day * 2,
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
        "kcal",
        "protein",
        "fat",
        "carb",
        "daily_score",
    }
    assert structured["comparisons"]["meal_mood_comparison"]["high_mood"]["protein_avg"] is not None
    assert "focus_rate" in structured["comparisons"]["notes_signal_comparison"]["high_mood"]
    assert "office_heavy_day_rate" in structured["comparisons"]["location_pattern_comparison"]["recent_7d"]
    assert "diary" not in structured["top_good_days"][0]
    assert "diary" not in structured["last_30_days_summary"]["daily_records"][0]


def test_generation_context_excludes_today_non_sleep_fields_from_inputs(monkeypatch) -> None:
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

    _patch_history_result(monkeypatch, generator, [today, yesterday])
    monkeypatch.setattr(
        generator,
        "read_daily_log",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("read_daily_log must not be called in this test")),
    )

    context = build_today_advice_generation_context(
        daily_log_read_url="read",
        bearer_token=None,
        target_date="2026-03-20",
    )

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
        assert "today sleep only / non-sleep historical only / must include recent 7-day trend" in prompt


def test_prompt_builders_include_sleep_only_and_recent_7d_constraints() -> None:
    from scripts.mood_advice_generator import build_final_user_prompt, build_judgment_user_prompt

    judgment_input = {
        "today_sleep": {"sleep_score": 70},
        "historical_behavior_patterns": {"recent_7d_avg": {"done_count_avg": 1.5}},
        "historical_recording_patterns": {"notes_recording_rate_7d": 0.4},
        "historical_context": {"recent_notes_samples": ["過去メモ"]},
    }
    structured = {
        "comparisons": {"recent_7d": {"done_count_avg": 1.5}},
        "last_30_days_summary": {"aggregates": {"all_days": {"count": 10}}},
        "top_good_days": [],
        "top_bad_days": [],
    }
    judgment_prompt = build_judgment_user_prompt(judgment_input=judgment_input, structured=structured)
    final_prompt = build_final_user_prompt(
        judgment_json={"recent_behavior_pattern": "直近7日でdone平均が高め", "recommended_actions": ["午前に1件着手"]},
        today_facts=judgment_input,
    )

    for prompt in (judgment_prompt, final_prompt):
        assert "today sleep only" in prompt
        assert "non-sleep historical only" in prompt
        assert "must include recent 7-day trend" in prompt


def test_generate_today_advice_ignores_same_day_non_sleep_zero_and_missing_fields(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator

    today = _summary(
        target_date="2026-03-20",
        notes=None,
        done_count=0,
        drop_count=0,
        expenses_total=0,
        meal_summary=None,
        meal_photos=[],
        location_summary=None,
        sleep_score=58,
        sleep_duration_min=340,
        sleep_analysis_jp="睡眠時間が短く、回復感も弱いです。",
        today_condition_forecast_jp="午前から集中が切れやすい見込みです。",
    )
    history = [
        today,
        _summary(target_date="2026-03-19", done_count=3, expenses_total=2400, meal_summary="定食", notes="午後に集中", mood="★★★★"),
        _summary(target_date="2026-03-18", done_count=2, expenses_total=2200, meal_summary="自炊", notes="午前に着手", mood="★★★★"),
        _summary(target_date="2026-03-17", done_count=4, expenses_total=2100, meal_summary="麺", notes="朝に整理", mood="★★★★★"),
        _summary(target_date="2026-03-16", done_count=3, expenses_total=2300, meal_summary="丼", notes="開始が早い", mood="★★★★"),
        _summary(target_date="2026-03-15", done_count=2, expenses_total=2000, meal_summary="魚", notes="メモが役立つ", mood="★★★★"),
        _summary(target_date="2026-03-14", done_count=3, expenses_total=2500, meal_summary="カレー", notes="外出少なめ", mood="★★★"),
        _summary(target_date="2026-03-13", done_count=3, expenses_total=2600, meal_summary="パスタ", notes="午前が安定", mood="★★★★"),
    ]

    _patch_history_result(monkeypatch, generator, history)
    monkeypatch.setattr(generator, "label_notes_in_batches", lambda **kwargs: {})
    monkeypatch.setattr(
        generator,
        "_chat_completion",
        lambda **kwargs: "睡眠時間は短く朝の立ち上がりは重めですが、直近7日ではdone数が2件台後半で、午前に着手した日のメモが安定していました。今日はその流れを再現する日として、午前の早い段階で最重要の1件に着手し、昼前に短い整理メモだけ残してください。",
    )

    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")

    assert result is not None
    advice = result.today_advice
    assert "睡眠" in advice
    assert len(advice) > 20
    assert "今日はメモがない" not in advice
    assert "タスク完了がゼロ" not in advice
    assert "食事記録がない" not in advice
    assert "支出が少ない" not in advice
    assert "停滞" not in advice
    assert any("睡眠" in item or "sleep" in item.lower() for item in result.judgment_json["evidence_used"])
    assert any("7日" in item or "done" in item.lower() for item in result.judgment_json["evidence_used"])


def test_generation_context_surfaces_recent_7d_non_sleep_patterns(monkeypatch) -> None:
    from scripts.mood_advice_generator import build_today_advice_generation_context

    today = _summary(target_date="2026-03-20", done_count=0, notes=None, meal_summary=None, expenses_total=0)
    prior_days = [
        _summary(target_date="2026-03-19", done_count=3, drop_count=1, expenses_total=3000, notes="n1", meal_summary="m1"),
        _summary(target_date="2026-03-18", done_count=2, drop_count=1, expenses_total=2500, notes="n2", meal_summary="m2"),
        _summary(target_date="2026-03-17", done_count=4, drop_count=0, expenses_total=2800, notes="n3", meal_summary=None, meal_photos=["p"]),
        _summary(target_date="2026-03-16", done_count=3, drop_count=2, expenses_total=2600, notes=None, meal_summary="m4"),
        _summary(target_date="2026-03-15", done_count=1, drop_count=1, expenses_total=2400, notes="n5", meal_summary="m5"),
        _summary(target_date="2026-03-14", done_count=2, drop_count=0, expenses_total=2200, notes=None, meal_summary="m6"),
        _summary(target_date="2026-03-13", done_count=3, drop_count=1, expenses_total=2100, notes="n7", meal_summary=None, meal_photos=["p"]),
        _summary(target_date="2026-03-12", done_count=1, drop_count=1, expenses_total=900, notes=None, meal_summary=None),
    ]

    import scripts.mood_advice_generator as generator
    _patch_history_result(monkeypatch, generator, [today, *prior_days])
    context = build_today_advice_generation_context(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")

    behavior = context["today_state"]["historical_behavior_patterns"]
    recording = context["today_state"]["historical_recording_patterns"]
    assert behavior["recent_7d_avg"]["done_count_avg"] is not None
    assert behavior["recent_7d_vs_30d"]["spend_total"] in {"up", "down", "flat"}
    assert recording["notes_recording_rate_7d"] == 0.71
    assert recording["meal_logged_rate_7d"] == 1.0


def test_generate_today_advice_prompt_omits_today_non_sleep_fields(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
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

    _patch_history_result(monkeypatch, generator, history)

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
    assert len(prompts) >= 1
    combined_prompt = "\n".join(prompts)
    assert "当日の場所" not in combined_prompt
    assert "sleep" in combined_prompt.lower()
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


def test_count_input_tokens_returns_estimate_without_tiktoken() -> None:
    from scripts.mood_advice_generator import _build_chat_messages, _count_input_tokens

    messages = _build_chat_messages(system_prompt="sys", user_prompt="user")
    count, method = _count_input_tokens(model="gpt-4.1", messages=messages)

    assert count is not None
    assert count > 0
    assert method in {"tiktoken", "estimated_chars_div4"}


def test_lightgbm_today_contribution_uses_selected_sleep_candidate_values() -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    from scripts.today_advice_lightgbm import run_lightgbm_low_mood

    rows = []
    for i in range(15):
        rows.append(
            {
                "date": f"2026-03-{i+1:02d}",
                "mood": 1 if i % 2 == 0 else 5,
                "sleep_hours": 10.0 if i == 14 else 6.0 + (i % 3) * 0.2,
                "sleep_score": 74.0 if i == 14 else 70.0 + (i % 4),
                "notes_sentiment_label": "negative" if i % 2 == 0 else "positive",
                "task_done_count": float(i % 5),
            }
        )

    import pandas as pd

    df = pd.DataFrame(rows)
    out = run_lightgbm_low_mood(
        df,
        today_feature_overrides={"sleep_hours": 7.1, "sleep_score": 94.0},
        today_feature_date="2026-03-28",
    )

    by_feature = {item["feature"]: item["today_value"] for item in out.get("today_contribution_features", [])}
    if "sleep_hours" in by_feature:
        assert by_feature["sleep_hours"] == 7.1
    if "sleep_score" in by_feature:
        assert by_feature["sleep_score"] == 94.0
    assert out.get("feature_row_date_used_as_today") == "2026-03-28"
    assert out.get("contribution_feature_scope") == "target_date_proxy"


def test_structured_comparison_includes_meal_notes_location_signals() -> None:
    from scripts.mood_advice_generator import _build_structured_comparison

    history = [
        _summary(target_date="2026-03-19", mood="★★★★★", kcal=2200, protein=140, fat=55, carb=250, notes="集中できたしジムにも行けた", location_summary="出社中心・カフェ", done_count=4),
        _summary(target_date="2026-03-18", mood="★★★★", kcal=2100, protein=130, fat=50, carb=240, notes="はかどった、回復感あり", location_summary="オフィス中心", done_count=3),
        _summary(target_date="2026-03-17", mood="★", kcal=1500, protein=70, fat=80, carb=160, notes="寝不足で眠いし夜食、ストレスあり", location_summary="自宅中心・夜まで外出", done_count=1),
        _summary(target_date="2026-03-16", mood="★★", kcal=1600, protein=75, fat=78, carb=170, notes="疲れと後悔で食べ過ぎ", location_summary="自宅中心→買い物→カフェ", done_count=1),
    ]

    structured = _build_structured_comparison(history)

    meal = structured["comparisons"]["meal_mood_comparison"]
    assert meal["high_mood"]["protein_avg"] == 135.0
    assert meal["low_mood"]["fat_avg"] == 79.0
    assert structured["comparisons"]["good_vs_bad_delta"]["protein"] == 62.5

    notes = structured["comparisons"]["notes_signal_comparison"]
    assert notes["high_mood"]["focus_rate"] == 1.0
    assert notes["low_mood"]["sleep_issue_rate"] == 0.5
    assert notes["good_vs_bad_delta"]["overeating_rate"] is not None

    location = structured["comparisons"]["location_pattern_comparison"]
    assert location["high_mood"]["office_heavy_day_rate"] == 1.0
    assert location["low_mood"]["home_heavy_day_rate"] == 1.0
    assert location["low_mood"]["late_outing_day_rate"] == 0.5


def test_generation_context_adds_structured_non_sleep_comparisons(monkeypatch) -> None:
    from scripts.mood_advice_generator import build_today_advice_generation_context
    import scripts.mood_advice_generator as generator

    today = _summary(target_date="2026-03-20", sleep_score=72, sleep_duration_min=420, notes=None, meal_summary=None, location_summary="当日")
    history = [
        today,
        _summary(target_date="2026-03-19", mood="★★★★★", kcal=2200, protein=140, fat=55, carb=250, notes="集中できた", location_summary="出社中心"),
        _summary(target_date="2026-03-18", mood="★", kcal=1500, protein=70, fat=80, carb=160, notes="寝不足で眠い", location_summary="自宅中心・夜まで外出"),
    ]
    _patch_history_result(monkeypatch, generator, history)
    context = build_today_advice_generation_context(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")

    assert context is not None
    structured = context["judgment_input"]["structured_historical_comparison"]["comparisons"]
    assert "meal_mood_comparison" in structured
    assert "notes_signal_comparison" in structured
    assert "location_pattern_comparison" in structured
    assert "today_sleep" in context["judgment_input"]
    assert "当日" not in json.dumps(context["judgment_input"], ensure_ascii=False)


def test_generate_today_advice_requires_recent_and_good_bad_evidence(monkeypatch) -> None:
    if not HAS_PANDAS:
        pytest.skip("pandas not installed")
    import scripts.mood_advice_generator as generator

    today = _summary(target_date="2026-03-20", sleep_score=60, sleep_duration_min=330, notes=None, meal_summary=None, location_summary=None)
    history = [
        today,
        _summary(target_date="2026-03-19", mood="★★★★★", kcal=2200, protein=140, fat=55, carb=250, notes="集中できたし運動した", location_summary="出社中心"),
        _summary(target_date="2026-03-18", mood="★★★★", kcal=2100, protein=130, fat=50, carb=240, notes="はかどった", location_summary="オフィス中心"),
        _summary(target_date="2026-03-17", mood="★", kcal=1500, protein=70, fat=80, carb=160, notes="寝不足で眠いし夜食", location_summary="自宅中心・夜まで外出"),
        _summary(target_date="2026-03-16", mood="★★", kcal=1600, protein=75, fat=78, carb=170, notes="ストレスで食べ過ぎ", location_summary="自宅中心"),
        _summary(target_date="2026-03-15", mood="★★★", kcal=1800, protein=90, fat=65, carb=200, notes="普通", location_summary="外出"),
        _summary(target_date="2026-03-14", mood="★★★★", kcal=2050, protein=120, fat=58, carb=225, notes="集中できた", location_summary="出社中心"),
        _summary(target_date="2026-03-13", mood="★★", kcal=1550, protein=72, fat=82, carb=165, notes="疲れと眠気", location_summary="自宅中心"),
    ]

    _patch_history_result(monkeypatch, generator, history)
    monkeypatch.setattr(generator, "label_notes_in_batches", lambda **kwargs: {})
    monkeypatch.setattr(
        generator,
        "_chat_completion",
        lambda **kwargs: "今日は睡眠時間が330分と短く、朝の立ち上がりは慎重に見たほうがよさそうです。直近7日ではfocus系メモとsleep_issue系メモが混在しつつ、良い日は高タンパク寄りで出社中心、悪い日は夜食や自宅中心が重なりやすい傾向があるため、今日はその差が広がりにくい流れを選ぶのが合いそうです。まず午前の早い段階で最重要1件に着手し、昼前に短いメモで状態を固定してください。",
    )

    result = generator.generate_today_advice(daily_log_read_url="read", bearer_token=None, target_date="2026-03-20")

    assert result is not None
    assert "睡眠" in result.today_advice
    assert len(result.today_advice) > 20
    assert any(item.startswith("sleep:") for item in result.judgment_json["evidence_used"])
    assert any(item.startswith("recent_7d:") for item in result.judgment_json["evidence_used"])
    assert len(result.judgment_json["evidence_used"]) >= 2
    assert "meal_signal" in result.judgment_json
    assert "notes_pattern_signal" in result.judgment_json
    assert "location_pattern_signal" in result.judgment_json
