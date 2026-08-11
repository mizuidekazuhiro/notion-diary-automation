from __future__ import annotations

from scripts.sleep_utils import (
    resolve_canonical_sleep_metrics,
    resolve_sleep_duration_minutes,
    resolve_sleep_target_date,
    resolve_sleep_for_target_date,
    validate_generated_sleep_text,
)
from types import SimpleNamespace
from scripts.today_advice_renderer import render_today_advice_from_analysis


def test_resolve_sleep_duration_from_start_end() -> None:
    resolved = resolve_sleep_duration_minutes(
        "2026-03-27T01:35:00+09:00",
        "2026-03-27T08:17:00+09:00",
        268,
    )
    assert resolved.resolved_sleep_duration_min == 268
    assert resolved.duration_source == "canonical_raw_duration"
    assert resolved.invalid_reason is None


def test_resolve_sleep_duration_prefers_canonical_raw_over_start_end() -> None:
    resolved = resolve_sleep_duration_minutes(
        "2026-03-27T01:35:00+09:00",
        "2026-03-27T08:17:00+09:00",
        268,
    )
    assert resolved.resolved_sleep_duration_min == 268


def test_resolve_sleep_duration_invalid_when_end_before_start() -> None:
    resolved = resolve_sleep_duration_minutes(
        "2026-03-27T08:17:00+09:00",
        "2026-03-27T01:35:00+09:00",
        None,
    )
    assert resolved.resolved_sleep_duration_min is None
    assert resolved.invalid_reason == "end_before_or_equal_start"


def test_resolve_sleep_target_date_with_0500_jst_boundary() -> None:
    got = resolve_sleep_target_date(
        sleep_start="2026-03-27T01:35:00+09:00",
        sleep_end="2026-03-27T08:17:00+09:00",
        fallback_date="2026-03-27",
    )
    assert got == "2026-03-26"


def test_day_boundary_exact_and_adjacent(monkeypatch) -> None:
    monkeypatch.setenv("SLEEP_DAY_BOUNDARY_HOUR", "5")
    assert resolve_sleep_target_date(sleep_start="2026-03-27T04:59:59+09:00", sleep_end=None) == "2026-03-26"
    assert resolve_sleep_target_date(sleep_start="2026-03-27T05:00:00+09:00", sleep_end=None) == "2026-03-27"
    assert resolve_sleep_target_date(sleep_start="2026-03-27T05:00:01+09:00", sleep_end=None) == "2026-03-27"


def test_today_sleep_never_falls_back_to_old_374_minutes() -> None:
    today = SimpleNamespace(target_date="2026-08-10", sleep_start=None, sleep_end=None, sleep_duration_min=None, sleep_score=None)
    old = SimpleNamespace(target_date="2026-07-20", sleep_start=None, sleep_end=None, sleep_duration_min=374, sleep_score=None)
    _, selected, source = resolve_sleep_for_target_date(
        target_date="2026-08-10", today_summary=today, history_summaries=[old]
    )
    assert selected is None
    assert source == "no_valid_candidate"

    body = render_today_advice_from_analysis(
        analysis_json={
            "today_sleep_context": {
                "sleep_available": selected is not None,
                "sleep_hours": None,
                "sleep_score": None,
                "sleep_invalid_reason": source,
            },
            "matched_patterns_count": 0,
            "recent_7d_summary": {"behavior_trend": []},
            "primary_focus": "focus",
        },
        model="unused",
        chat_completion=lambda **_: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
    )
    assert "6.23" not in body
    assert "374" not in body


def test_resolve_canonical_sleep_metrics_with_text() -> None:
    metrics = resolve_canonical_sleep_metrics(
        "2026-03-27T01:35:00+09:00",
        "2026-03-27T08:17:00+09:00",
        268,
    )
    assert metrics.resolved_sleep_duration_min == 268
    assert metrics.resolved_sleep_duration_hours == 4.47
    assert metrics.resolved_sleep_duration_text == "4時間28分"
    assert metrics.sleep_duration_source == "canonical_raw_duration"


def test_validate_generated_sleep_text_rejects_stale_duration() -> None:
    result = validate_generated_sleep_text(
        "深夜1時35分から朝8時17分までの約4時間28分でした。",
        canonical_sleep_duration_min=402.0,
        canonical_sleep_duration_text="6時間42分",
    )
    assert result.is_consistent is False
    assert result.found_duration_text == "4時間28分"
