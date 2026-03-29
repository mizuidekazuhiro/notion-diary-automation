from __future__ import annotations

from types import SimpleNamespace

from publish.email_templates import render_daily_log_html, render_daily_log_text
from publish.read_daily_log import read_daily_log
from scripts.daily_job import _weather_roundtrip_status


def test_read_daily_log_weather_summary_and_raw_fields_are_available(monkeypatch) -> None:
    def _fake_fetch_json(url: str, bearer_token: str | None) -> dict[str, object]:
        del url, bearer_token
        return {
            "found": True,
            "target_date": "2026-03-29",
            "page_id": "page",
            "title": "Daily Log",
            "summary_text": "",
            "summary_html": "",
            "mail_id": "run",
            "weather": "弱い雨。最高17.4℃、最低8.9℃、降水確率は100%です。",
            "weather_temp_max_c": 17.4,
            "weather_temp_min_c": 8.9,
            "weather_precip_probability_max": 100,
            "weather_code": 61,
            "expenses": {"total": 0, "count": 0, "top": [], "remaining": 0},
        }

    monkeypatch.setattr("publish.read_daily_log.fetch_json", _fake_fetch_json)
    summary = read_daily_log(daily_log_read_url="https://example.com/api/daily_log", target_date="2026-03-29", bearer_token=None)

    assert summary is not None
    assert summary.weather_summary == "弱い雨。最高17.4℃、最低8.9℃、降水確率は100%です。"
    assert summary.weather_code == 61
    assert summary.weather_temp_max_c == 17.4
    assert summary.weather_temp_min_c == 8.9
    assert summary.weather_precip_probability_max == 100


def test_email_templates_render_weather_section_for_html_and_text_with_fallback() -> None:
    payload = {
        "target_date": "2026-03-29",
        "summary_text": "🎉\n- A (Priority: High)",
        "diary": "日記",
        "meal_summary": "ごはん",
        "weather_summary": "",
        "weather_code": 61,
        "weather_temp_max_c": 17.4,
        "weather_temp_min_c": 8.9,
        "weather_precip_probability_max": 100,
    }
    html = render_daily_log_html(payload)
    text = render_daily_log_text(payload)

    assert "Weather" in html
    assert "Weather" in text
    assert "弱い雨。最高17.4℃、最低8.9℃、降水確率は100%です。" in html
    assert "弱い雨。最高17.4℃、最低8.9℃、降水確率は100%です。" in text
    assert "未取得" not in html
    assert "未取得" not in text


def test_weather_compare_ignores_timestamp_second_precision_only() -> None:
    summary = SimpleNamespace(
        weather_summary="くもり。最高15.2℃、最低7.1℃です。",
        weather_location="東京",
        weather_retrieved_at="2026-03-29T05:14:00+00:00",
        weather_input_hash="abc",
        weather_generated_at="2026-03-29T05:14:00+00:00",
        weather_temp_max_c=15.2,
        weather_temp_min_c=7.1,
        weather_precip_probability_max=40,
        weather_code=3,
    )
    expected_payload = {
        "weather": "くもり。最高15.2℃、最低7.1℃です。",
        "weather_summary": "くもり。最高15.2℃、最低7.1℃です。",
        "weather_location": "東京",
        "weather_retrieved_at": "2026-03-29T05:14:58+00:00",
        "weather_input_hash": "abc",
        "weather_generated_at": "2026-03-29T05:14:58+00:00",
        "weather_temp_max_c": 15.2,
        "weather_temp_min_c": 7.1,
        "weather_precip_probability_max": 40,
        "weather_code": 3,
    }

    status = _weather_roundtrip_status(summary=summary, expected_payload=expected_payload)
    assert status["readback_ok"] is True
    assert status["compare_ok"] is True
    assert status["compare_normalized"] is True
    assert "weather_retrieved_at" in status["ignored_fields"]
    assert "weather_generated_at" in status["ignored_fields"]
