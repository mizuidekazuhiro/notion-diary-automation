from __future__ import annotations

import json

import pytest
import requests

from scripts import f_risk_generator
from scripts import openai_chat_utils
from scripts.today_advice_renderer import render_today_advice_from_analysis


def test_today_advice_renderer_uses_analysis_json_as_primary_input() -> None:
    captured: dict[str, str] = {}

    def _fake_chat_completion(**kwargs: str) -> str:
        captured["user_prompt"] = kwargs["user_prompt"]
        return "分析JSONに沿った短い助言です。"

    analysis_json = {
        "target_date": "2026-03-28",
        "today_sleep_context": {"sleep_available": True, "sleep_should_mention": True, "sleep_hours": 6.5},
        "recent_7d_summary": {"behavior_trend": ["直近7日で夜更かし2回"]},
        "matched_patterns_count": 1,
    }
    out = render_today_advice_from_analysis(analysis_json=analysis_json, model="x", chat_completion=_fake_chat_completion)
    assert out.strip()
    assert "analysis=" in captured["user_prompt"]
    assert "daily_records" not in captured["user_prompt"]


def test_f_risk_alert_generation_uses_model_result_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def _fake_chat_completion(**kwargs: str) -> str:
        captured["prompt"] = kwargs["user_prompt"]
        return "F支出パターンとの一致があり、購入判断は一拍置くのが安全です。"

    monkeypatch.setattr(f_risk_generator, "chat_completion", _fake_chat_completion)
    text, fallback_used, reason = f_risk_generator._render_f_risk_alert(
        risk_json={
            "risk_matched": True,
            "score": 0.82,
            "matched_patterns": ["睡眠時間が短め"],
            "explanation_points": ["直近数日で短睡眠が続き、過去F日と中程度一致"],
            "skipped_reason": None,
        },
        model="x",
    )
    assert text
    assert fallback_used is False
    assert reason is None
    assert "risk_json=" in captured["prompt"]


def test_openai_chat_retry_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {"choices": [{"message": {"content": "ok"}}]}

    def _fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ReadTimeout("timeout")
        return _Resp()

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "2")
    monkeypatch.setattr(openai_chat_utils.requests, "post", _fake_post)
    out = openai_chat_utils.chat_completion(model="x", system_prompt="s", user_prompt="u")
    assert out == "ok"
    assert calls["n"] == 2


def test_openai_chat_final_failure_is_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def _always_timeout(*args, **kwargs):
        raise requests.ReadTimeout("timeout")

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "1")
    monkeypatch.setattr(openai_chat_utils.requests, "post", _always_timeout)
    with pytest.raises(RuntimeError):
        openai_chat_utils.chat_completion(model="x", system_prompt="s", user_prompt="u")


def test_f_risk_labeling_failed_sets_skip_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: [object()] * 20)
    def _fake_labeler(**kwargs):
        kwargs["audit"]["labeling_failed"] = True
        return {}
    monkeypatch.setattr(f_risk_generator, "label_notes_in_batches", _fake_labeler)
    result = f_risk_generator.generate_f_risk(daily_log_read_url="r", bearer_token=None, target_date="2026-03-28")
    assert result.skip_reason == "labeling_failed"
    assert result.alert_text is None


def test_f_risk_insufficient_samples_does_not_emit_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f_risk_generator, "_load_histories", lambda **kwargs: [object()] * 5)
    result = f_risk_generator.generate_f_risk(daily_log_read_url="r", bearer_token=None, target_date="2026-03-28")
    assert result.skip_reason == "insufficient_samples"
    assert result.alert_text is None
