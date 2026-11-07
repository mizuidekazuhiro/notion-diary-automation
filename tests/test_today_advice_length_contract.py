"""The former minimum-length contract caused fabricated, repetitive advice."""
from types import SimpleNamespace
import json

import pytest

from scripts.daily_mail_quality import build_quality_report, today_advice_length_valid
from scripts.today_advice_renderer import render_today_advice_result


@pytest.mark.parametrize("length", [20, 194, 201, 210, 220, 380])
def test_short_supported_output_is_kept_without_padding(length):
    generated = "あ" * length
    result = render_today_advice_result(analysis_json={"target_counts": {"valid": 12}, "evidence_used": ["observed"]},
        model="test", chat_completion=lambda **kw: generated)
    assert result.text == generated
    assert result.status == "success" and not result.fallback_used
    assert today_advice_length_valid(result.text)


@pytest.mark.parametrize("length", [0, 381, 420])
def test_empty_or_overlong_output_records_rejection(length):
    result = render_today_advice_result(analysis_json={"evidence_used": ["observed"]}, model="test",
        chat_completion=lambda **kw: "あ" * length)
    assert result.status == "degraded" and result.fallback_used
    assert result.rejection_reason == ("empty_generation" if length == 0 else "output_too_long")
    assert len(result.text) < 100
    assert "20〜30分" not in result.text


def test_insufficiency_is_short_and_does_not_create_length_warning_or_trend():
    result = render_today_advice_result(analysis_json={"target_counts": {"valid": 0}}, model="test",
        chat_completion=lambda **kw: pytest.fail("no evidence must not call AI"))
    summary = SimpleNamespace(target_date="2026-11-05", today_advice=result.text,
        generation_metadata=json.dumps({"advice": {"status": result.status, "fallback_used": False}}))
    report = build_quality_report(summary, mail_plain_text="Today advice", mail_html="<p>Today advice</p>")
    codes = {issue["code"] for issue in report["issues"]}
    assert {"health_no_data", "today_sleep_no_data"} <= codes
    assert "today_advice_length_out_of_range" not in codes
    assert "today_advice_missing_trend_evidence" not in codes
    assert result.status == "insufficient_data" and not result.fallback_used


def test_configured_minimum_cannot_force_padding_but_maximum_is_enforced(monkeypatch):
    monkeypatch.setenv("DAILY_MAIL_TODAY_ADVICE_MIN_CHARS", "240")
    monkeypatch.setenv("DAILY_MAIL_TODAY_ADVICE_MAX_CHARS", "360")
    def chat(**kw):
        assert "上限360字" in kw["user_prompt"]
        return "記録から新たな提案はありません。"
    result = render_today_advice_result(analysis_json={"evidence_used": ["observed"]}, model="test", chat_completion=chat)
    assert result.status == "success"
    assert result.text == "記録から新たな提案はありません。"
