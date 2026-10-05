from types import SimpleNamespace

import pytest

from scripts.daily_mail_quality import build_quality_report, today_advice_length_valid
from scripts.today_advice_renderer import render_today_advice_from_analysis


@pytest.mark.parametrize("length", [201, 210, 194, 194, 0, 381, 420])
def test_out_of_range_generation_uses_fallback_without_filling_health(length):
    # Reproduce all four incident lengths with the same missing-sleep state.
    analysis = {
        "today_sleep_context": {"sleep_available": False, "sleep_hours": None},
        "matched_patterns_count": 1,
        "primary_focus": "回復優先",
        "recent_7d_summary": {"behavior_trend": ["直近7日で夜遅い外出が0回", "疲労系Notesが0日"]},
    }
    generated = "あ" * length
    text = render_today_advice_from_analysis(
        analysis_json=analysis, model="test", chat_completion=lambda **kwargs: generated,
    )
    assert text != generated
    assert today_advice_length_valid(text)
    assert "直近7日で夜遅い外出が0回" in text
    assert "睡眠" not in text
    summary = SimpleNamespace(target_date="2026-10-04", today_advice=text)
    report = build_quality_report(summary, mail_plain_text="Today advice", mail_html="")
    codes = {issue["code"] for issue in report["issues"]}
    assert {"health_no_data", "today_sleep_no_data"} <= codes
    assert "today_advice_length_out_of_range" not in codes
    assert analysis["today_sleep_context"]["sleep_hours"] is None


@pytest.mark.parametrize("length", [220, 380])
def test_boundary_generation_is_preserved(length):
    generated = "あ" * length
    assert render_today_advice_from_analysis(
        analysis_json={"matched_patterns_count": 1}, model="test",
        chat_completion=lambda **kwargs: generated,
    ) == generated


def test_compact_count_and_configured_prompt_use_same_limits(monkeypatch):
    monkeypatch.setenv("DAILY_MAIL_TODAY_ADVICE_MIN_CHARS", "240")
    monkeypatch.setenv("DAILY_MAIL_TODAY_ADVICE_MAX_CHARS", "360")
    generated = "あ" * 125 + " \n" + "あ" * 125
    def chat(**kwargs):
        assert "240〜360" in kwargs["user_prompt"]
        assert "空白・改行を除いて" in kwargs["user_prompt"]
        return generated
    assert render_today_advice_from_analysis(
        analysis_json={"matched_patterns_count": 1}, model="test", chat_completion=chat,
    ) == generated.strip()


@pytest.mark.parametrize("failure", ["exception", "no_pattern"])
def test_existing_fallback_paths_meet_default_length(failure):
    def chat(**kwargs):
        raise RuntimeError("unavailable")
    text = render_today_advice_from_analysis(
        analysis_json={
            "matched_patterns_count": 0 if failure == "no_pattern" else 1,
            "today_sleep_context": {"sleep_available": False},
        }, model="test", chat_completion=chat,
    )
    assert today_advice_length_valid(text)
