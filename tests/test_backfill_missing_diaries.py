from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scripts import backfill_missing_diaries as backfill


def test_generate_target_dates_jst_example() -> None:
    assert backfill.default_end_date(datetime(2026, 7, 13, 8, tzinfo=ZoneInfo("Asia/Tokyo"))) == "2026-07-12"
    assert backfill.generate_target_dates(end_date="2026-07-12", days=7) == [
        "2026-07-06",
        "2026-07-07",
        "2026-07-08",
        "2026-07-09",
        "2026-07-10",
        "2026-07-11",
        "2026-07-12",
    ]


def test_existing_page_skips_all_phases(monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    calls: list[list[str]] = []
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: SimpleNamespace(page_id="page-1"))
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: calls.append(args))

    stats = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=False)

    assert stats.existing_count == 1
    assert stats.success_count == 0
    assert calls == []
    assert "status=existing_skipped" in caplog.text


def test_missing_page_runs_phase_a_b_c_with_target_date_and_no_phase_d(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: None)
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: calls.append(args))

    stats = backfill.run_backfill(days=1, end_date="2026-07-08", dry_run=False)

    assert stats.missing_count == 1
    assert stats.success_count == 1
    assert [c[1:] for c in calls] == [
        ["scripts/daily_job.py", "--phase", "ingest", "--target-date", "2026-07-08"],
        ["apps/location_summary_writer/src/main.py", "--target-date", "2026-07-08"],
        ["scripts/daily_job.py", "--phase", "notify_diary", "--target-date", "2026-07-08", "--backfill"],
    ]
    assert all("publish" not in c for c in calls)


def test_check_error_is_not_treated_as_missing(monkeypatch, caplog) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: calls.append(args))

    stats = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=False)

    assert stats.failed_count == 1
    assert stats.missing_count == 0
    assert calls == []
    assert "status=check_failed" in caplog.text


def test_multiple_days_continue_after_failure(monkeypatch) -> None:
    commands: list[str] = []
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: None)

    def fake_run(args: list[str], *, target_date: str) -> None:
        commands.append(target_date)
        if target_date == "2026-07-11":
            raise RuntimeError("phase failed")

    monkeypatch.setattr(backfill, "_run_command", fake_run)

    stats = backfill.run_backfill(days=2, end_date="2026-07-12", dry_run=False)

    assert stats.failed_count == 1
    assert stats.success_count == 1
    assert "2026-07-12" in commands


def test_dry_run_missing_does_not_run_phases(monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    calls: list[list[str]] = []
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: None)
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: calls.append(args))

    stats = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=True)

    assert stats.dry_run_count == 1
    assert calls == []
    assert "status=dry_run_missing" in caplog.text


def test_idempotent_second_run_skips_after_page_exists(monkeypatch) -> None:
    calls: list[list[str]] = []
    reads = iter([None, SimpleNamespace(page_id="created")])
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: next(reads))
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: calls.append(args))

    first = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=False)
    second = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=False)

    assert first.success_count == 1
    assert second.existing_count == 1
    assert len(calls) == 3
