from __future__ import annotations

import logging

from scripts import mood_advice_generator as generator


class _Summary:
    def __init__(self, date: str) -> None:
        self.target_date = date
        self.notes = ""


def test_load_daily_logs_for_period_skips_runtime_error(monkeypatch) -> None:
    calls: list[str] = []

    def fake_read_daily_log(*, daily_log_read_url: str, target_date: str, bearer_token):
        calls.append(target_date)
        if target_date == "2026-05-07":
            raise RuntimeError("fetch_json failed")
        return _Summary(target_date)

    monkeypatch.setattr(generator, "read_daily_log", fake_read_daily_log)
    result = generator._load_daily_logs_for_period_with_debug(
        daily_log_read_url="read", bearer_token=None, target_date="2026-05-08", days=3
    )

    assert [item.target_date for item in result.summaries] == ["2026-05-08", "2026-05-06"]
    assert result.failed_dates == ["2026-05-07"]
    assert result.missing_dates == []
    assert calls == ["2026-05-08", "2026-05-07", "2026-05-06"]


def test_load_daily_logs_for_period_marks_missing_without_partial(monkeypatch) -> None:
    def fake_read_daily_log(*, daily_log_read_url: str, target_date: str, bearer_token):
        if target_date == "2026-05-07":
            return None
        return _Summary(target_date)

    monkeypatch.setattr(generator, "read_daily_log", fake_read_daily_log)
    result = generator._load_daily_logs_for_period_with_debug(
        daily_log_read_url="read", bearer_token=None, target_date="2026-05-08", days=2
    )

    assert result.failed_dates == []
    assert result.missing_dates == ["2026-05-07"]


def test_load_daily_logs_for_period_include_next_day_default_and_true(monkeypatch) -> None:
    calls: list[str] = []

    def fake_read_daily_log(*, daily_log_read_url: str, target_date: str, bearer_token):
        calls.append(target_date)
        return _Summary(target_date)

    monkeypatch.setattr(generator, "read_daily_log", fake_read_daily_log)

    generator.load_daily_logs_for_period(daily_log_read_url="read", bearer_token=None, target_date="2026-05-08", days=1)
    assert calls == ["2026-05-08"]

    calls.clear()
    generator.load_daily_logs_for_period(
        daily_log_read_url="read", bearer_token=None, target_date="2026-05-08", days=1, include_next_day=True
    )
    assert calls == ["2026-05-09", "2026-05-08"]


def test_load_daily_logs_for_period_logs_skipped_date_on_429_equivalent(monkeypatch, caplog) -> None:
    def fake_read_daily_log(*, daily_log_read_url: str, target_date: str, bearer_token):
        raise RuntimeError("fetch_json failed")

    monkeypatch.setattr(generator, "read_daily_log", fake_read_daily_log)
    caplog.set_level(logging.WARNING)

    result = generator._load_daily_logs_for_period_with_debug(
        daily_log_read_url="read", bearer_token=None, target_date="2026-05-08", days=1
    )

    assert result.summaries == []
    assert result.failed_dates == ["2026-05-08"]
    assert "today_advice_history_read_skipped" in caplog.text


def test_build_today_advice_context_history_debug_separates_failed_and_missing(monkeypatch) -> None:
    today = _Summary("2026-05-08")
    prior = _Summary("2026-05-06")
    monkeypatch.setattr(
        generator,
        "_load_daily_logs_for_period_with_debug",
        lambda **kwargs: generator.TodayAdviceHistoryLoadResult(
            summaries=[today, prior],
            requested_dates=["2026-05-08", "2026-05-07", "2026-05-06"],
            failed_dates=["2026-05-07"],
            missing_dates=[],
            include_next_day=False,
        ),
    )
    monkeypatch.setattr(generator, "_resolve_today_sleep_candidates", lambda **kwargs: ([], None, "missing"))
    monkeypatch.setattr(generator, "_build_structured_comparison", lambda *_args, **_kwargs: {"counts": {}, "comparisons": {}, "last_30_days_summary": {}, "top_good_days": [], "top_bad_days": [], "high_mood_sample_count": 0, "low_mood_sample_count": 0})
    monkeypatch.setattr(generator, "_build_today_state", lambda *_args, **_kwargs: {"today_sleep": {}, "historical_behavior_patterns": {}, "historical_recording_patterns": {}, "historical_context": {}})

    context = generator.build_today_advice_generation_context(
        daily_log_read_url="read", bearer_token=None, target_date="2026-05-08"
    )

    assert context is not None
    assert context["history_debug"]["history_failed_count"] == 1
    assert context["history_debug"]["history_missing_count"] == 0
    assert context["history_debug"]["history_partial"] is True
    assert context["history_debug"]["history_incomplete"] is True
