from __future__ import annotations

from scripts.sleep_utils import (
    resolve_canonical_sleep_metrics,
    resolve_sleep_duration_minutes,
    resolve_sleep_target_date,
    validate_generated_sleep_text,
)


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
