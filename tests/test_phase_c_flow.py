from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from publish.read_daily_log import DailyLogSummary, ExpenseSummary
from scripts import daily_job, voice_diary_notes
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
        title="Daily Logï½œ2026-03-20",
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
        location_summary="è‡ªå®…ä¸­å¿ƒ",
        mood="â˜…â˜…â˜…",
        notes="åˆå‰ã¯çœ æ°—ã‚ã‚Š",
        weight=None,
        sleep_start="2026-03-19T23:30:00+09:00",
        sleep_end="2026-03-20T07:00:00+09:00",
        sleep_duration_min=450,
        resolved_sleep_duration_min=450,
        resolved_sleep_duration_hours=7.5,
        resolved_sleep_duration_text="7æ™‚é–“30åˆ†",
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
        weather_summary="æ™´ã‚Œã€‚æœ€é«˜25.0â„ƒã€æœ€ä½Ž15.0â„ƒã€é™æ°´é‡0.0mmã§ã™ã€‚",
        weather_location="æ±äº¬",
        weather_retrieved_at="2026-03-20T00:00:00Z",
        weather_generated_at="2026-03-20T00:01:00Z",
        weather_input_hash="abc",
        weather_temp_max_c=25.0,
        weather_temp_min_c=15.0,
        weather_precip_probability_max=50.0,  # stale value in Notion
        weather_code=0,
    )
    expected_payload = {
        "weather": "æ™´ã‚Œã€‚æœ€é«˜25.0â„ƒã€æœ€ä½Ž15.0â„ƒã€é™æ°´é‡0.0mmã§ã™ã€‚",
        "weather_summary": "æ™´ã‚Œã€‚æœ€é«˜25.0â„ƒã€æœ€ä½Ž15.0â„ƒã€é™æ°´é‡0.0mmã§ã™ã€‚",
        "weather_location": "æ±äº¬",
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
            "historical_context": {"recent_7d_location_samples": ["è‡ªå®…ä¸­å¿ƒ"]},
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

    assert result == summÛn¶¶‰žËkºwµç@€€±…ÍÍµ•Ñ¡½4(€€€€€€€‘•˜¹½Ü¡±Ì°Ñèõ9½¹”¤è€€ŒÑåÁ”è¥¹½É•m½Ù•ÉÉ¥‘•t4(€€€€€€€€€€€‘•°Ñè4(€€€€€€€€€€€É•ÑÕÉ¸±Ì ÈÀÈØ°€Ì°€Èä°€ÄÔ°€ÌÔ°Ñé¥¹™¼õi½¹•%¹™¼ ‰Í¥„½Q½­å¼ˆ¤¤4(4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰‘…Ñ•Ñ¥µ”ˆ°}É½é•¹…Ñ•Ñ¥µ”¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}Í…Ù•}‘…¥±å}±½}™¥•±‘Ìˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌèÍ…Ù•}…±±Ì¹…ÁÁ•¹¡­Ý…ÉÌ¤½Èì‰ÕÁ‘…Ñ•ˆèQÉÕ”°€‰É•…Í½¸ˆè€‰ÕÁ‘…Ñ•‰ô¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}É•™É•Í¡}‘…¥±å}±½}ÍÕµµ…Éäˆ°±…µ‰‘„½¹™¥œ°Ñ…É•Ñ}‘…Ñ”èÉ•™É•Í¡•¤4(4(€€€‘…¥±å}©½ˆ¹}•¹•É…Ñ•}…¹‘}Í…Ù•}Ý•…Ñ¡•È¡½¹™¥œ°ÍÕµµ…ÉäõÍÕµµ…Éä°ÉÕ¹}¥ô‰ÉÕ¸ˆ¤4(4(€€€…ÍÍ•ÉÐÝ•…Ñ¡•É}™•Ñ¡}…±±ÍlÁul‰Ñ…É•Ñ}‘…Ñ”‰t€ôô€ˆÈÀÈØ´ÀÌ´Èäˆ4(€€€…ÍÍ•ÉÐÍ…Ù•}…±±ÍlÁul‰Ñ…É•Ñ}‘…Ñ”‰t€ôô€ˆÈÀÈØ´ÀÌ´ÈÀˆ4(€€€Á…å±½…€ôÍ…Ù•}…±±ÍlÁul‰Á…å±½…‰t4(€€€…ÍÍ•ÉÐÁ…å±½…‘l‰Ý•…Ñ¡•È‰t€ôô€‹šfÓŽ
0ˆ4(€€€…ÍÍ•ÉÐÁ…å±½…‘l‰Ý•…Ñ¡•É}ÍÕµµ…Éä‰t€ôô€‹šfÓŽ
0ˆ4(€€€…ÍÍ•ÉÐÁ…å±½…‘l‰Ý•…Ñ¡•É}±½…Ñ¥½¸‰t€ôô€‹švÇ’ê°ˆ4(€€€…ÍÍ•ÉÐÁ…å±½…‘l‰Ý•…Ñ¡•É}Ñ•µÁ}µ…á}Œ‰t€ôô€ÈÌ¸À4(€€€…ÍÍ•ÉÐÁ…å±½…‘l‰Ý•…Ñ¡•É}Ñ•µÁ}µ¥¹}Œ‰t€ôô€ÄÔ¸À4(€€€…ÍÍ•ÉÐÁ…å±½…‘l‰Ý•…Ñ¡•É}ÁÉ•¥Á}ÁÉ½‰…‰¥±¥Ñå}µ…à‰t¥Ì9½¹”4(€€€…ÍÍ•ÉÐÁ…å±½…‘l‰Ý•…Ñ¡•É}½‘”‰t€ôô€À4(4(4)‘•˜Ñ•ÍÑ}Ý•…Ñ¡•É}¥¹ÁÕÑ}¡…Í¡}¡…¹•Í}Ý¡•¹}™½É•…ÍÑ}‘…Ñ•}¡…¹•Ì ¤€´ø9½¹”è4(€€€‰…Í•}Á…å±½…€ôì4(€€€€€€€€‰‘…¥±å}±½}Ñ…É•Ñ}‘…Ñ”ˆè€ˆÈÀÈØ´ÀÌ´Èàˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}™½É•…ÍÑ}‘…Ñ•}©ÍÐˆè€ˆÈÀÈØ´ÀÌ´Èäˆ°4(€€€€€€€€‰±½…Ñ¥½¹}¹…µ”ˆè€‹švÇ’ê°ˆ°4(€€€€€€€€‰±½…Ñ¥½¹}±…Ñ¥ÑÕ‘”ˆè€ÌÔ¸À°4(€€€€€€€€‰±½…Ñ¥½¹}±½¹¥ÑÕ‘”ˆè€ÄÌä¸À°4(€€€€€€€€‰±½…Ñ¥½¹}É•Í½±ÕÑ¥½¹}µ•Ñ¡½ˆè€‰±½…Ñ¥½¹}±½}±…Ñ•ÍÑ}±…Ñ±½¸ˆ°4(€€€€€€€€‰±½…Ñ¥½¹}Í½ÕÉ”ˆè€‰±½…Ñ¥½¹}±½}‘ˆˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}ÁÉ½Ù¥‘•Èˆè€‰½Á•¸µµ•Ñ•¼µ©µ„ˆ°4(€€€ô4(€€€¹•áÑ}Á…å±½…€ôì¨©‰…Í•}Á…å±½…°€‰Ý•…Ñ¡•É}ÁÉ½Ù¥‘•Èˆè€‰±•…äµÁÉ½Ù¥‘•È‰ô4(4(€€€™¥ÉÍÑ}¡…Í °|°|€ô‘…¥±å}©½ˆ¹}‰Õ¥±‘}¥¹ÁÕÑ}¡…Í ¡‰…Í•}Á…å±½…¤4(€€€Í•½¹‘}¡…Í °|°|€ô‘…¥±å}©½ˆ¹}‰Õ¥±‘}¥¹ÁÕÑ}¡…Í ¡¹•áÑ}Á…å±½…¤4(4(€€€…ÍÍ•ÉÐ™¥ÉÍÑ}¡…Í €„ôÍ•½¹‘}¡…Í 4(4(4)‘•˜Ñ•ÍÑ}Ý•…Ñ¡•É}É½Õ¹‘ÑÉ¥Á}‘…Ñ•Ñ¥µ•}¹½Éµ…±¥é…Ñ¥½¹}…•ÁÑÍ}é}…¹‘}ÕÑ}½™™Í•Ð ¤€´ø9½¹”è4(€€€•áÁ•Ñ•‘}Á…å±½…€ôì4(€€€€€€€€‰Ý•…Ñ¡•Èˆè€‹šfÓŽ
0ˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}ÍÕµµ…Éäˆè€‹šfÓŽ
0ˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}É•ÑÉ¥•Ù•‘}…Ðˆè€ˆÈÀÈØ´ÀÌ´ÈåPÀÈèÔÐèÐÝhˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}•¹•É…Ñ•‘}…Ðˆè€ˆÈÀÈØ´ÀÌ´ÈåPÀÈèÔÐèÐÝhˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}¥¹ÁÕÑ}¡…Í ˆè€‰¡…Í ´Äˆ°4(€€€ô4(€€€ÍÕµµ…Éä€ô}ÍÕµµ…Éä 4(€€€€€€€Ý•…Ñ¡•É}ÍÕµµ…Éäô‹šfÓŽ
0ˆ°4(€€€€€€€Ý•…Ñ¡•É}É•ÑÉ¥•Ù•‘}…ÐôˆÈÀÈØ´ÀÌ´ÈåPÀÈèÔÐèÐÜ¬ÀÀèÀÀˆ°4(€€€€€€€Ý•…Ñ¡•É}•¹•É…Ñ•‘}…ÐôˆÈÀÈØ´ÀÌ´ÈåPÀÈèÔÐèÐÜ¬ÀÀèÀÀˆ°4(€€€€€€€Ý•…Ñ¡•É}¥¹ÁÕÑ}¡…Í ô‰¡…Í ´Äˆ°4(€€€€¤4(4(€€€ÍÑ…ÑÕÌ€ô‘…¥±å}©½ˆ¹}Ý•…Ñ¡•É}É½Õ¹‘ÑÉ¥Á}ÍÑ…ÑÕÌ¡ÍÕµµ…ÉäõÍÕµµ…Éä°•áÁ•Ñ•‘}Á…å±½…õ•áÁ•Ñ•‘}Á…å±½…¤4(4(€€€…ÍÍ•ÉÐÍÑ…ÑÕÍl‰É•…‘‰…­}½¬‰t¥ÌQÉÕ”4(€€€…ÍÍ•ÉÐÍÑ…ÑÕÍl‰½µÁ…É•}½¬‰t¥ÌQÉÕ”4(€€€…ÍÍ•ÉÐÍÑ…ÑÕÍl‰µ¥ÍÍ¥¹}™¥•±‘Ì‰t€ôômt4(€€€…ÍÍ•ÉÐÍÑ…ÑÕÍl‰µ¥Íµ…Ñ¡}™¥•±‘Ì‰t€ôômt4(4(4)‘•˜Ñ•ÍÑ}Ý•…Ñ¡•É}É½Õ¹‘ÑÉ¥Á}Ñ•áÑ}…¹‘}¹Õµ‰•É}™¥•±‘Í}µ…Ñ¡}•¹‘}Ñ½}•¹ ¤€´ø9½¹”è4(€€€•áÁ•Ñ•‘}Á…å±½…€ôì4(€€€€€€€€‰Ý•…Ñ¡•Èˆè€‹šfÓŽ
0ˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}ÍÕµµ…Éäˆè€‹šfÓŽ
0ˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}±½…Ñ¥½¸ˆè€‹švÇ’ê°ˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}Ñ•µÁ}µ…á}Œˆè€ÈÌ¸À°4(€€€€€€€€‰Ý•…Ñ¡•É}Ñ•µÁ}µ¥¹}Œˆè€ÄÔ¸À°4(€€€€€€€€‰Ý•…Ñ¡•É}ÁÉ•¥Á}ÁÉ½‰…‰¥±¥Ñå}µ…àˆè9½¹”°4(€€€€€€€€‰Ý•…Ñ¡•É}½‘”ˆè€À°4(€€€€€€€€‰Ý•…Ñ¡•É}É•ÑÉ¥•Ù•‘}…Ðˆè€ˆÈÀÈØ´ÀÌ´ÈÁPÀÀèÀÀèÀÁhˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}¥¹ÁÕÑ}¡…Í ˆè€‰¡…Í ´Äˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}•¹•É…Ñ•‘}…Ðˆè€ˆÈÀÈØ´ÀÌ´ÈÁPÀÀèÀÄèÀÁhˆ°4(€€€ô4(€€€ÍÕµµ…Éä€ô}ÍÕµµ…Éä 4(€€€€€€€Ý•…Ñ¡•É}ÍÕµµ…Éäô‹šfÓŽ
0ˆ°4(€€€€€€€Ý•…Ñ¡•É}±½…Ñ¥½¸ô‹švÇ’ê°ˆ°4(€€€€€€€Ý•…Ñ¡•É}Ñ•µÁ}µ…á}ŒôÈÌ¸À°4(€€€€€€€Ý•…Ñ¡•É}Ñ•µÁ}µ¥¹}ŒôÄÔ¸À°4(€€€€€€€Ý•…Ñ¡•É}ÁÉ•¥Á}ÁÉ½‰…‰¥±¥Ñå}µ…àõ9½¹”°4(€€€€€€€Ý•…Ñ¡•É}½‘”ôÀ°4(€€€€€€€Ý•…Ñ¡•É}É•ÑÉ¥•Ù•‘}…ÐôˆÈÀÈØ´ÀÌ´ÈÁPÀÀèÀÀèÀÀ¬ÀÀèÀÀˆ°4(€€€€€€€Ý•…Ñ¡•É}¥¹ÁÕÑ}¡…Í ô‰¡…Í ´Äˆ°4(€€€€€€€Ý•…Ñ¡•É}•¹•É…Ñ•‘}…ÐôˆÈÀÈØ´ÀÌ´ÈÁPÀÀèÀÄèÀÀ¬ÀÀèÀÀˆ°4(€€€€¤4(4(€€€ÍÑ…ÑÕÌ€ô‘…¥±å}©½ˆ¹}Ý•…Ñ¡•É}É½Õ¹‘ÑÉ¥Á}ÍÑ…ÑÕÌ¡ÍÕµµ…ÉäõÍÕµµ…Éä°•áÁ•Ñ•‘}Á…å±½…õ•áÁ•Ñ•‘}Á…å±½…¤4(4(€€€…ÍÍ•ÉÐÍÑ…ÑÕÍl‰É•…‘‰…­}½¬‰t¥ÌQÉÕ”4(€€€…ÍÍ•ÉÐÍÑ…ÑÕÍl‰½µÁ…É•}½¬‰t¥ÌQÉÕ”4(€€€…ÍÍ•ÉÐÍÑ…ÑÕÍl‰µ¥ÍÍ¥¹}™¥•±‘Ì‰t€ôômt4(€€€…ÍÍ•ÉÐÍÑ…ÑÕÍl‰µ¥Íµ…Ñ¡}™¥•±‘Ì‰t€ôômt4(4(4)‘•˜Ñ•ÍÑ}Ý½É­•ÉÍ}‘…¥±å}±½}É•…‘}Ý•…Ñ¡•É}É•Í½±ÕÑ¥½¹}ÕÍ•Í}•á…Ñ}¹…µ•Í}…¹‘}Ù…±Õ•}™…±±‰…¬ ¤€´ø9½¹”è4(€€€Í½ÕÉ”€ôA…Ñ  ‰Ý½É­•ÉÌ½ÍÉŒ½¥¹‘•à¹ÑÌˆ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤4(4(€€€…ÍÍ•ÉÐ€‰É•Í½±Ù•á…ÑAÉ½Á•ÉÑå9…µ”¡q¸€€€ÁÉ½Á•ÉÑ¥•Ì±q¸€€€•Ñ]•…Ñ¡•ÉAÉ½Á•ÉÑå9…µ”¡•¹Ø¤±q¸€€€p‰‘…¥±å}±½}É•…éÝ•…Ñ¡•Épˆ°ˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰É•Í½±Ù•á…ÑAÉ½Á•ÉÑå9…µ”¡q¸€€€ÁÉ½Á•ÉÑ¥•Ì±q¸€€€•Ñ]•…Ñ¡•ÉMÕµµ…ÉåAÉ½Á•ÉÑå9…µ”¡•¹Ø¤±q¸€€€p‰‘…¥±å}±½}É•…éÝ•…Ñ¡•É}ÍÕµµ…Éåpˆ°ˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰mp‰]•…Ñ¡•ÈMÕµµ…Éåpˆ°p‰Ý•…Ñ¡•Épˆ°p‰Ý•…Ñ¡•É}ÍÕµµ…Éåp‰tˆ¹½Ð¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰mp‰]•…Ñ¡•Épˆ°p‰Ý•…Ñ¡•Épˆ°p‰Ý•…Ñ¡•É}ÍÕµµ…Éåp‰tˆ¹½Ð¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€ˆü•ÑMÑÉ¥¹É½µAÉ½Á•ÉÑä¡ÁÉ½Á•ÉÑ¥•ÍmÝ•…Ñ¡•ÉAÉ½Á•ÉÑå9…µ•t¤ˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€ˆü•ÑMÑÉ¥¹É½µAÉ½Á•ÉÑä¡ÁÉ½Á•ÉÑ¥•ÍmÝ•…Ñ¡•ÉMÕµµ…ÉåAÉ½Á•ÉÑå9…µ•t¤ˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰½¹ÍÐÝ•…Ñ¡•ÉMÕµµ…Éä€ô€¡Ý•…Ñ¡•ÉMÕµµ…ÉåI•Í½±Ù•‘Q•áÐñðÝ•…Ñ¡•ÉI•Í½±Ù•‘Q•áÐñð¹Õ±°¤ìˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰½¹ÍÐÝ•…Ñ¡•É1•…åQ•áÐ€ô€¡Ý•…Ñ¡•ÉI•Í½±Ù•‘Q•áÐñðÝ•…Ñ¡•ÉMÕµµ…ÉåI•Í½±Ù•‘Q•áÐñð¹Õ±°¤ìˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰™Õ¹Ñ¥½¸•Ñ%Í½MÑÉ¥¹É½µAÉ½Á•ÉÑä¡ÁÉ½Á•ÉÑäèI•½ÉñÍÑÉ¥¹œ°…¹äøðÕ¹‘•™¥¹•¤èÍÑÉ¥¹œìˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€ˆü•Ñ%Í½MÑÉ¥¹É½µAÉ½Á•ÉÑä¡ÁÉ½Á•ÉÑ¥•ÍmÝ•…Ñ¡•ÉI•ÑÉ¥•Ù•‘ÑAÉ½Á•ÉÑå9…µ•t¤ˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€ˆü•Ñ%Í½MÑÉ¥¹É½µAÉ½Á•ÉÑä¡ÁÉ½Á•ÉÑ¥•ÍmÝ•…Ñ¡•É•¹•É…Ñ•‘ÑAÉ½Á•ÉÑå9…µ•t¤ˆ¥¸Í½ÕÉ”4(4(4(4(4)‘•˜Ñ•ÍÑ}Ý½É­•ÉÍ}‘…¥±å}±½}É•…‘}¥¹±Õ‘•Í}ÍÑÕ‘å}™¥•±‘Í}¥¹}É•ÍÁ½¹Í” ¤€´ø9½¹”è4(€€€Í½ÕÉ”€ôA…Ñ  ‰Ý½É­•ÉÌ½ÍÉŒ½¥¹‘•à¹ÑÌˆ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤4(4(€€€…ÍÍ•ÉÐ€ˆˆ‰É•Í½±Ù•AÉ½Á•ÉÑå9…µ” 4(€€€ÁÉ½Á•ÉÑ¥•Ì°4(€€€p‰MÑÕ‘ä5¥¹ÕÑ•Ípˆ°4(€€€p‰‘…¥±å}±½}É•…éÍÑÕ‘å}µ¥¹ÕÑ•Ípˆ°4(€€€mp‰ÍÑÕ‘å}µ¥¹ÕÑ•Íp‰t°ˆˆˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€ˆˆ‰É•Í½±Ù•AÉ½Á•ÉÑå9…µ” 4(€€€ÁÉ½Á•ÉÑ¥•Ì°4(€€€p‰MÑÕ‘äM•ÍÍ¥½¹Ípˆ°4(€€€p‰‘…¥±å}±½}É•…éÍÑÕ‘å}Í•ÍÍ¥½¹Ípˆ°4(€€€mp‰ÍÑÕ‘å}Í•ÍÍ¥½¹Íp‰t°ˆˆˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€ˆˆ‰É•Í½±Ù•AÉ½Á•ÉÑå9…µ” 4(€€€ÁÉ½Á•ÉÑ¥•Ì°4(€€€p‰MÑÕ‘ä1…ÍÐUÍ•Ñpˆ°4(€€€p‰‘…¥±å}±½}É•…éÍÑÕ‘å}±…ÍÑ}ÕÍ•‘}…Ñpˆ°4(€€€mp‰ÍÑÕ‘å}±…ÍÑ}ÕÍ•‘}…Ñp‰t°ˆˆˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰ÍÑÕ‘å}µ¥¹ÕÑ•ÌèÍÑÕ‘å5¥¹ÕÑ•Ì°ˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰ÍÑÕ‘å}Í•ÍÍ¥½¹ÌèÍÑÕ‘åM•ÍÍ¥½¹Ì°ˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰ÍÑÕ‘å}±…ÍÑ}ÕÍ•‘}…ÐèÍÑÕ‘å1…ÍÑUÍ•‘Ð°ˆ¥¸Í½ÕÉ”4(4(4)‘•˜Ñ•ÍÑ}Ý½É­•ÉÍ}‘…¥±å}±½}É•…‘}ÍÑÕ‘å}±…ÍÑ}ÕÍ•‘}…Ñ}¹Õ±±}™…±±‰…­}¥Í}•áÁ±¥¥Ð ¤€´ø9½¹”è4(€€€Í½ÕÉ”€ôA…Ñ  ‰Ý½É­•ÉÌ½ÍÉŒ½¥¹‘•à¹ÑÌˆ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤4(4(€€€…ÍÍ•ÉÐ€‰•Ñ…Ñ•Q¥µ•É½µAÉ½Á•ÉÑä¡ÁÉ½Á•ÉÑ¥•ÍmÍÑÕ‘å1…ÍÑUÍ•‘ÑAÉ½Á•ÉÑå9…µ•t¤ñðˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰•ÑMÑÉ¥¹É½µAÉ½Á•ÉÑä¡ÁÉ½Á•ÉÑ¥•ÍmÍÑÕ‘å1…ÍÑUÍ•‘ÑAÉ½Á•ÉÑå9…µ•t¤ñðˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰¹Õ±±q¸€€€€è¹Õ±°ìˆ¥¸Í½ÕÉ”4(4(4)‘•˜Ñ•ÍÑ}Ý½É­•ÉÍ}•¹•É…Ñ•}‘¥…Éå}Ý•…Ñ¡•É}ÍÕµµ…Éå}…¹‘}Í•±•Ñ}…É•}Í•Á…É…Ñ• ¤€´ø9½¹”è4(€€€Í½ÕÉ”€ôA…Ñ  ‰Ý½É­•ÉÌ½ÍÉŒ½¥¹‘•à¹ÑÌˆ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤4(€€€…ÍÍ•ÉÐ€‰½¹ÍÐÝ•…Ñ¡•ÉMÕµµ…ÉåQ•áÑI•Í½±Ù•€ôˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰½¹ÍÐÝ•…Ñ¡•ÉM•±•Ñ1…‰•°€ô¥¹™•É]•…Ñ¡•ÉM•±•Ñ1…‰•°¡Ý•…Ñ¡•É½‘”°Ý•…Ñ¡•ÉMÕµµ…ÉåQ•áÑI•Í½±Ù•¤ìˆ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€Ý•…Ñ¡•ÉM•±•ÑM­¥ÁI•…Í½¸€ô€‰Í•±•Ñ}½ÁÑ¥½¹}¹½Ñ}™½Õ¹ˆìœ¥¸Í½ÕÉ”4(€€€…ÍÍ•ÉÐ€‰ÕÁ‘…Ñ•‘}Ý¥Ñ¡}Ý•…Ñ¡•É}Í•±•Ñ}Í­¥Àˆ¥¸Í½ÕÉ”4(4(4)‘•˜Ñ•ÍÑ}‰Õ¥±‘}¥¹ÁÕÑ}¡…Í¡}¹½Éµ…±¥é•Í}•µÁÑå}Ù…±Õ•Ì ¤€´ø9½¹”è4(€€€Á…å±½…‘}„€ôì4(€€€€€€€€‰¹½Ñ•Ìˆè€ˆ¡•±±¼€ˆ°4(€€€€€€€€‰¥Ñ•µÌˆèl‰„ˆ°€ˆˆ°€ˆ€€‰t°4(€€€€€€€€‰¹•ÍÑ•ˆèì‰àˆè€ˆˆ°€‰äˆè9½¹”°€‰èˆè€Ä¸Áô°4(€€€ô4(€€€Á…å±½…‘}ˆ€ôì4(€€€€€€€€‰¹½Ñ•Ìˆè€‰¡•±±¼ˆ°4(€€€€€€€€‰¥Ñ•µÌˆèl‰„‰t°4(€€€€€€€€‰¹•ÍÑ•ˆèì‰èˆè€Åô°4(€€€ô4(4(€€€¡…Í¡}„°¹½Éµ…±¥é•‘}„°|€ô‘…¥±å}©½ˆ¹}‰Õ¥±‘}¥¹ÁÕÑ}¡…Í ¡Á…å±½…‘}„¤4(€€€¡…Í¡}ˆ°¹½Éµ…±¥é•‘}ˆ°|€ô‘…¥±å}©½ˆ¹}‰Õ¥±‘}¥¹ÁÕÑ}¡…Í ¡Á…å±½…‘}ˆ¤4(4(€€€…ÍÍ•ÉÐ¡…Í¡}„€ôô¡…Í¡}ˆ4(€€€…ÍÍ•ÉÐ¹½Éµ…±¥é•‘}„€ôô¹½Éµ…±¥é•‘}ˆ€ôôì4(€€€€€€€€‰¥Ñ•µÌˆèl‰„‰t°4(€€€€€€€€‰¹•ÍÑ•ˆèì‰èˆè€Åô°4(€€€€€€€€‰¹½Ñ•Ìˆè€‰¡•±±¼ˆ°4(€€€ô4(4(4)‘•˜Ñ•ÍÑ}Ý½É­™±½Ý}ÉÕ¹}¹…µ•Í}µ…Ñ¡}‘•Á•¹‘•¹¥•Ì ¤€´ø9½¹”è4(€€€Ý½É­™±½Ý}‘¥È€ôA…Ñ  ˆ¹¥Ñ¡Õˆ½Ý½É­™±½ÝÌˆ¤4(€€€¥¹•ÍÐ€ô€¡Ý½É­™±½Ý}‘¥È€¼€‰¥¹•ÍÑ}‘…¥±å}±½œ¹åµ°ˆ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤4(€€€±½…Ñ¥½¸€ô€¡Ý½É­™±½Ý}‘¥È€¼€‰±½…Ñ¥½¹}ÍÕµµ…Éä¹åµ°ˆ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤4(€€€‘¥…Éä€ô€¡Ý½É­™±½Ý}‘¥È€¼€‰‘¥…Éå}¹½Ñ¥™ä¹åµ°ˆ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤4(€€€ÁÕ‰±¥Í €ô€¡Ý½É­™±½Ý}‘¥È€¼€‰ÁÕ‰±¥Í¡}‘…¥±å}µ…¥°¹åµ°ˆ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤4(4(€€€…ÍÍ•ÉÐ€‰¹…µ”è…¥±ä¥…Éä€ÀÄ€´%¹•ÍÐ…¥±ä1½œˆ¥¸¥¹•ÍÐ4(€€€…ÍÍ•ÉÐ€ˆ´…¥±ä¥…Éä€ÀÄ€´%¹•ÍÐ…¥±ä1½œˆ¥¸±½…Ñ¥½¸4(€€€…ÍÍ•ÉÐ€‰¹…µ”è…¥±ä¥…Éä€ÀÈ€´•¹•É…Ñ”1½…Ñ¥½¸MÕµµ…Éäˆ¥¸±½…Ñ¥½¸4(€€€…ÍÍ•ÉÐ€ˆ´…¥±ä¥…Éä€ÀÈ€´•¹•É…Ñ”1½…Ñ¥½¸MÕµµ…Éäˆ¥¸‘¥…Éä4(€€€…ÍÍ•ÉÐ€‰¹…µ”è…¥±ä¥…Éä€ÀÌ€´•¹•É…Ñ”¥…Éä€˜M±••À%¹Í¥¡ÑÌˆ¥¸‘¥…Éä4(€€€…ÍÍ•ÉÐ€ˆ´…¥±ä¥…Éä€ÀÌ€´•¹•É…Ñ”¥…Éä€˜M±••À%¹Í¥¡ÑÌˆ¥¸ÁÕ‰±¥Í 4(4(4)‘•˜Ñ•ÍÑ}¹½Ñ¥™å}‘¥…Éå}‘½•Í}¹½Ñ}…±±}µ…¥±}¹½Ñ¥™¥…Ñ¥½¹}É•¹‘•É•É}½É}Í•¹‘•È¡µ½¹­•åÁ…Ñ ¤€´ø9½¹”è4(€€€ÍÕµµ…Éä€ô}ÍÕµµ…Éä¡‘¥…Éäô‰•¹•É…Ñ•‘¥…Éäˆ¤4(€€€½¹™¥œ€ô}½¹™¥œ¡‘…¥±å}±½}É•…‘}ÕÉ°ô‰É•…ˆ°‰•…É•É}Ñ½­•¸õ9½¹”°‘¥…Éå}•¹•É…Ñ•}ÕÉ°ô‰•¸ˆ°‘¥…Éå}µ…É­}¹½Ñ¥™¥•‘}ÕÉ°ô‰µ…É¬ˆ¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}É•™É•Í¡}‘…¥±å}±½}ÍÕµµ…Éäˆ°±…µ‰‘„½¹™¥œ°Ñ…É•Ñ}‘…Ñ”èÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}Ý•…Ñ¡•Èˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥°€¨©­Ý…ÉÌèÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}™}É¥Í¬ˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥°€¨©­Ý…ÉÌèÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}Í±••Á}¥¹Í¥¡ÑÌˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥°€¨©­Ý…ÉÌèÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}Ñ½‘…å}…‘Ù¥”ˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥°€¨©­Ý…ÉÌèÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}‘¥…Éäˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥°€¨©­Ý…ÉÌèÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰É•¹‘•É}‘¥…Éå}¹½Ñ¥™¥…Ñ¥½¹}µ…¥°ˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌè€¡|™½È|¥¸€ ¤¤¹Ñ¡É½Ü¡ÍÍ•ÉÑ¥½¹ÉÉ½È ‰µÕÍÐ¹½ÐÉ•¹‘•È¹½Ñ¥™¥…Ñ¥½¸µ…¥°ˆ¤¤¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰Í•¹‘}µ…¥°ˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌè€¡|™½È|¥¸€ ¤¤¹Ñ¡É½Ü¡ÍÍ•ÉÑ¥½¹ÉÉ½È ‰µÕÍÐ¹½ÐÍ•¹µ…¥°¥¸¹½Ñ¥™å}‘¥…Éäˆ¤¤¤4(€€€‘…¥±å}©½ˆ¹ÉÕ¹}¹½Ñ¥™å}‘¥…Éä¡½¹™¥œ°€ˆÈÀÈØ´ÀÌ´ÈÀˆ°€‰ÉÕ¸ˆ¤4(4(4)‘•˜Ñ•ÍÑ}±½…‘}½¹™¥}¹½Ñ¥™å}‘¥…Éå}‘½•Í}¹½Ñ}É•ÅÕ¥É•}µ…¥±}•¹Ø¡µ½¹­•åÁ…Ñ ¤€´ø9½¹”è4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰Á…ÉÍ•}…ÉÌˆ°±…µ‰‘„èM¥µÁ±•9…µ•ÍÁ…”¡Á¡…Í”ô‰¹½Ñ¥™å}‘¥…Éäˆ°Ñ…É•Ñ}‘…Ñ”ôˆÈÀÈØ´ÀÌ´ÈÀˆ¤¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰ÉÕ¹}¹½Ñ¥™å}‘¥…Éäˆ°±…µ‰‘„½¹™¥œ°Ñ…É•Ñ}‘…Ñ”°ÉÕ¹}¥è9½¹”¤4(€€€µ½¹­•åÁ…Ñ ¹‘•±•¹Ø ‰5%1}I=4ˆ°É…¥Í¥¹œõ…±Í”¤4(€€€µ½¹­•åÁ…Ñ ¹‘•±•¹Ø ‰5%1}Q<ˆ°É…¥Í¥¹œõ…±Í”¤4(€€€µ½¹­•åÁ…Ñ ¹‘•±•¹Ø ‰5%1}AA}AMM]=Iˆ°É…¥Í¥¹œõ…±Í”¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ•¹Ø ‰%1e}1=}UAMIQ}UI0ˆ°€‰¡ÑÑÁÌè¼½•á…µÁ±”¹½´½…Á¤½‘…¥±å}±½œ½ÕÁÍ•ÉÐˆ¤4(€€€µ½¹­•åÁ…Ñ ¹‘•±•¹Ø ‰QM-M}1=M}UI0ˆ°É…¥Í¥¹œõ…±Í”¤4(€€€‘…¥±å}©½ˆ¹µ…¥¸ ¤4(4(4)‘•˜Ñ•ÍÑ}Í±••Á}¥¹Í¥¡ÑÍ}¹½}Ù½¥•}¹½Ñ•Í}¹…µ••ÉÉ½È¡µ½¹­•åÁ…Ñ ¤€´ø9½¹”è4(€€€ÍÕµµ…Éä€ô}ÍÕµµ…Éä ¤4(€€€½¹™¥œ€ô}½¹™¥œ¡‘…¥±å}±½}É•…‘}ÕÉ°ô‰É•…ˆ°‰•…É•É}Ñ½­•¸õ9½¹”°‘¥…Éå}•¹•É…Ñ•}ÕÉ°ô‰•¸ˆ¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰É•Í½±Ù•}Í±••Á}™½É}Ñ…É•Ñ}‘…Ñ”ˆ°±…µ‰‘„€¨©­Ý…ÉÌè€¡mt°ì‰É•Í½±Ù•‘}Í±••Á}‘ÕÉ…Ñ¥½¹}µ¥¸ˆè€ÐÈÀ°€‰É…Ý}Í±••Á}‘ÕÉ…Ñ¥½¹}µ¥¸ˆè€ÐÈÀ°€‰Í±••Á}ÍÑ…ÉÐˆèÍÕµµ…Éä¹Í±••Á}ÍÑ…ÉÐ°€‰Í±••Á}•¹ˆèÍÕµµ…Éä¹Í±••Á}•¹°€‰Í±••Á}Í½É”ˆèÍÕµµ…Éä¹Í±••Á}Í½É”°€‰‘ÕÉ…Ñ¥½¹}Í½ÕÉ”ˆè€‰É•Í½±Ù•‰ô°€‰É•Í½±Ù•ˆ¤¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰±½…‘}É••¹Ñ}‘…¥±å}±½Ìˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌèmt¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰µ…å‰•}•¹•É…Ñ•}Í±••Á}¥¹Í¥¡ÑÌˆ°±…µ‰‘„€¨©­Ý…ÉÌèíô¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}É•™É•Í¡}‘…¥±å}±½}ÍÕµµ…Éäˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌèÍÕµµ…Éä¤4(€€€½ÕÐ€ô‘…¥±å}©½ˆ¹}•¹•É…Ñ•}…¹‘}Í…Ù•}Í±••Á}¥¹Í¥¡ÑÌ¡½¹™¥œ°ÍÕµµ…ÉäõÍÕµµ…Éä°ÉÕ¹}¥ô‰ÉÕ¸ˆ¤4(€€€…ÍÍ•ÉÐ½ÕÐ€ôôÍÕµµ…Éä4(4(4)‘•˜Ñ•ÍÑ}‘¥…Éå}Í…Ù•}ÍÕ•ÍÍ}µ…É­Í}Ù½¥•}¹½Ñ•Í}ÕÍ•¡µ½¹­•åÁ…Ñ ¤€´ø9½¹”è4(€€€ÍÕµµ…Éä€ô}ÍÕµµ…Éä¡‘¥…Éäõ9½¹”°‘¥…Éå}¥¹ÁÕÑ}¡…Í õ9½¹”¤4(€€€½¹™¥œ€ô}½¹™¥œ¡‘…¥±å}±½}É•…‘}ÕÉ°ô‰É•…ˆ°‰•…É•É}Ñ½­•¸õ9½¹”°‘¥…Éå}•¹•É…Ñ•}ÕÉ°ô‰•¸ˆ¤4(€€€¹½Ñ”€ôÙ½¥•}‘¥…Éå}¹½Ñ•Ì¹Y½¥•¥…Éå9½Ñ”¡Á…•}¥ô‰ØÄˆ°É•½É‘•‘}…ÐôˆÈÀÈØ´ÀÌ´ÈÁPÀÀèÀÀèÀÀ¬ÀäèÀÀˆ°Ñ•áÐô‰àˆ°Í½ÕÉ”ô‰¥½Ìˆ°¹½Ñ•}¡…Í ô‰ ˆ¤4(€€€…±±•€ôì‰µ…É­•ˆè€Áô4(4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰™•Ñ¡}Ù½¥•}‘¥…Éå}¹½Ñ•Ìˆ°±…µ‰‘„Ñ…É•Ñ}‘…Ñ”èm¹½Ñ•t¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰•¹•É…Ñ•}‘¥…Éå}™É½µ}‘…¥±å}±½œˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌè€‰¹•Ü‘¥…Éäˆ¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}Í…Ù•}‘…¥±å}±½}™¥•±‘Ìˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌèì‰ÕÁ‘…Ñ•ˆèQÉÕ”°€‰É•…Í½¸ˆè€‰½¬‰ô¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}É•™É•Í¡}‘…¥±å}±½}ÍÕµµ…Éäˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌèÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰µ…É­}Ù½¥•}‘¥…Éå}¹½Ñ•Í}ÕÍ•ˆ°±…µ‰‘„¹½Ñ•Ì°€¨°‘…¥±å}±½}Á…•}¥è…±±•¹}}Í•Ñ¥Ñ•µ}| ‰µ…É­•ˆ°…±±•‘l‰µ…É­•‰t€¬±•¸¡±¥ÍÐ¡¹½Ñ•Ì¤¤¤¤4(4(€€€‘…¥±å}©½ˆ¹}•¹•É…Ñ•}…¹‘}Í…Ù•}‘¥…Éä¡½¹™¥œ°ÍÕµµ…ÉäõÍÕµµ…Éä°ÉÕ¹}¥ô‰ÉÕ¸ˆ¤4(€€€…ÍÍ•ÉÐ…±±•‘l‰µ…É­•‰t€ôô€Ä4(4(4)‘•˜Ñ•ÍÑ}‘¥…Éå}Í­¥Á}‘½•Í}¹½Ñ}µ…É­}Ù½¥•}¹½Ñ•Í}ÕÍ•¡µ½¹­•åÁ…Ñ ¤€´ø9½¹”è4(€€€¹½Ñ”€ôÙ½¥•}‘¥…Éå}¹½Ñ•Ì¹Y½¥•¥…Éå9½Ñ”¡Á…•}¥ô‰ØÄˆ°É•½É‘•‘}…ÐôˆÈÀÈØ´ÀÌ´ÈÁPÀÀèÀÀèÀÀ¬ÀäèÀÀˆ°Ñ•áÐô‰àˆ°Í½ÕÉ”ô‰¥½Ìˆ°¹½Ñ•}¡…Í ô‰ ˆ¤4(€€€ÍÕµµ…Éä€ô}ÍÕµµ…Éä¡‘¥…Éäô‰•á¥ÍÑ¥¹œˆ¤4(€€€™¥•±‘Ì°|°|°|€ô‘…¥±å}©½ˆ¹‰Õ¥±‘}‘¥…Éå}¥¹ÁÕÑ}™¥•±‘Ì¡ÍÕµµ…Éä°Ù½¥•}‘¥…Éå}¹½Ñ•Í}Ñ•áÐô‰lÀÀèÀÁtàˆ¤4(€€€Á…å±½…°|€ô‘…¥±å}©½ˆ¹}‰Õ¥±‘}‘¥…Éå}¡…Í¡}Á…å±½…¡ÍÕµµ…Éä°™¥•±‘Ì¤4(€€€ÕÉÉ•¹Ñ}¡…Í °|°|€ô‘…¥±å}©½ˆ¹}‰Õ¥±‘}¥¹ÁÕÑ}¡…Í ¡Á…å±½…¤4(€€€ÍÕµµ…Éä€ô}ÍÕµµ…Éä¡‘¥…Éäô‰•á¥ÍÑ¥¹œˆ°‘¥…Éå}¥¹ÁÕÑ}¡…Í õÕÉÉ•¹Ñ}¡…Í ¤4(€€€½¹™¥œ€ô}½¹™¥œ¡‘…¥±å}±½}É•…‘}ÕÉ°ô‰É•…ˆ°‰•…É•É}Ñ½­•¸õ9½¹”°‘¥…Éå}•¹•É…Ñ•}ÕÉ°ô‰•¸ˆ¤4(€€€…±±•€ôì‰µ…É­•ˆè€Áô4(4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰™•Ñ¡}Ù½¥•}‘¥…Éå}¹½Ñ•Ìˆ°±…µ‰‘„Ñ…É•Ñ}‘…Ñ”èm¹½Ñ•t¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}É•™É•Í¡}‘…¥±å}±½}ÍÕµµ…Éäˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌèÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰•¹•É…Ñ•}‘¥…Éå}™É½µ}‘…¥±å}±½œˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌè€¡|™½È|¥¸€ ¤¤¹Ñ¡É½Ü¡ÍÍ•ÉÑ¥½¹ÉÉ½È ‰Í¡½Õ±Í­¥Àˆ¤¤¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰µ…É­}Ù½¥•}‘¥…Éå}¹½Ñ•Í}ÕÍ•ˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌè…±±•¹}}Í•Ñ¥Ñ•µ}| ‰µ…É­•ˆ°€Ä¤¤4(4(€€€‘…¥±å}©½ˆ¹}•¹•É…Ñ•}…¹‘}Í…Ù•}‘¥…Éä¡½¹™¥œ°ÍÕµµ…ÉäõÍÕµµ…Éä°ÉÕ¹}¥ô‰ÉÕ¸ˆ¤4(€€€…ÍÍ•ÉÐ…±±•‘l‰µ…É­•‰t€ôô€À4(4)‘•˜Ñ•ÍÑ}‰…­™¥±±}µ½‘•}Í­¥ÁÍ}Ý•…Ñ¡•È¡µ½¹­•åÁ…Ñ ¤€´ø9½¹”è4(€€€½É‘•Èè±¥ÍÑmÍÑÉt€ômt4(€€€ÍÕµµ…Éä€ô}ÍÕµµ…Éä ¤4(€€€½¹™¥œ€ô}½¹™¥œ¡‘…¥±å}±½}É•…‘}ÕÉ°ô‰É•…ˆ°‰•…É•É}Ñ½­•¸õ9½¹”°‘¥…Éå}•¹•É…Ñ•}ÕÉ°ô‰•¸ˆ°‘¥…Éå}µ…É­}¹½Ñ¥™¥•‘}ÕÉ°ô‰µ…É¬ˆ¤4(4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}É•™É•Í¡}‘…¥±å}±½}ÍÕµµ…Éäˆ°±…µ‰‘„½¹™¥œ°Ñ…É•Ñ}‘…Ñ”èÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}Ý•…Ñ¡•Èˆ°±…µ‰‘„€©…ÉÌ°€¨©­Ý…ÉÌè€¡|™½È|¥¸€ ¤¤¹Ñ¡É½Ü¡ÍÍ•ÉÑ¥½¹ÉÉ½È ‰Ý•…Ñ¡•ÈÍ¡½Õ±‰”Í­¥ÁÁ•ˆ¤¤¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}½µÁÕÑ•}•áÁ•¹Í•}™}…±•ÉÐˆ°±…µ‰‘„€¨°ÍÕµµ…Éä°ÉÕ¹}¥è½É‘•È¹…ÁÁ•¹ ‰•áÁ•¹Í•}˜ˆ¤½Èì‰µ…Ñ¡•ˆè…±Í”°€‰É•…Í½¹Ìˆèmuô¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}Í±••Á}¥¹Í¥¡ÑÌˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥è½É‘•È¹…ÁÁ•¹ ‰Í±••Àˆ¤½ÈÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}™}É¥Í¬ˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥è½É‘•È¹…ÁÁ•¹ ‰™}É¥Í¬ˆ¤½ÈÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}Ñ½‘…å}…‘Ù¥”ˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥è½É‘•È¹…ÁÁ•¹ ‰…‘Ù¥”ˆ¤½ÈÍÕµµ…Éä¤4(€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡‘…¥±å}©½ˆ°€‰}•¹•É…Ñ•}…¹‘}Í…Ù•}‘¥…Éäˆ°±…µ‰‘„½¹™¥œ°€¨°ÍÕµµ…Éä°ÉÕ¹}¥°€¨©­Ý…ÉÌè½É‘•È¹…ÁÁ•¹ ‰‘¥…Éäˆ¤½ÈÍÕµµ…Éä¤4(4(€€€‘…¥±å}©½ˆ¹ÉÕ¹}¹½Ñ¥™å}‘¥…Éä¡½¹™¥œ°€ˆÈÀÈØ´ÀÌ´ÈÀˆ°€‰ÉÕ¸ˆ°‰…­™¥±°õQÉÕ”¤4(4(€€€…ÍÍ•ÉÐ½É‘•È€ôôl‰•áÁ•¹Í•}˜ˆ°€‰Í±••Àˆ°€‰™}É¥Í¬ˆ°€‰…‘Ù¥”ˆ°€‰‘¥…Éä‰t4