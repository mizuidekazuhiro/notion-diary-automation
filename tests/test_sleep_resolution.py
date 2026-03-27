from __future__ import annotations

from scripts.sleep_utils import resolve_sleep_duration_minutes, resolve_sleep_target_date


def test_resolve_sleep_duration_from_start_end() -> None:
    resolved = resolve_sleep_duration_minutes(
        "2026-03-27T01:35:00+09:00",
        "2026-03-27T08:17:00+09:00",
        268,
    )
    assert resolved.resolved_sleep_duration_min == 402.0
    assert resolved.duration_source == "derived_from_start_end"
    assert resolved.invalid_reason is None


def test_resolve_sleep_duration_prefers_start_end_over_raw() -> None:
    resolved = resolve_sleep_duration_minutes(
        "2026-03-27T01:35:00+09:00",
        "2026-03-27T08:17:00+09:00",
        268,
    )
    assert resolved.resolved_sleep_duration_min == 402.0


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
