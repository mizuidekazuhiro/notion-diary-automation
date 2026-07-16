from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts import backfill_missing_diaries as backfill


def summary(**overrides):
    base = dict(
        target_date="2026-07-12",
        date="2026-07-12",
        target_date_value="2026-07-12",
        target_date_property_present=True,
        activity_summary="summary",
        mail_id="run-id",
        today_advice="advice",
        diary="diary",
        today_advice_generated_at="2026-07-12T07:00:00+09:00",
        diary_generated_at="2026-07-12T07:05:00+09:00",
        page_id="page-1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_generate_target_dates_jst_example() -> None:
    assert backfill.default_end_date(datetime(2026, 7, 13, 8, tzinfo=ZoneInfo("Asia/Tokyo"))) == "2026-07-12"
    assert backfill.generate_target_dates(end_date="2026-07-12", days=7) == [
        "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-11", "2026-07-12",
    ]


def test_classification_missing_incomplete_complete() -> None:
    assert backfill.classify_daily_log(None)[0] == "missing"
    assert backfill.classify_daily_log(summary())[0] == "complete"
    assert backfill.classify_daily_log(summary(diary=""))[0] == "incomplete"


def test_study_only_page_is_incomplete() -> None:
    item = summary(target_date_value="", activity_summary="", mail_id="", today_advice="", diary="", today_advice_generated_at="", diary_generated_at="", study_minutes=30)
    classification, missing = backfill.classify_daily_log(item)
    assert classification == "incomplete"
    assert "target_date_value" in missing
    assert "diary" in missing


def test_target_date_property_absent_is_incomplete() -> None:
    classification, missing = backfill.classify_daily_log(summary(target_date_value=None, target_date_property_present=False))
    assert classification == "incomplete"
    assert "target_date_value" in missing


def test_dry_run_missing_does_not_run_phases(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: None)
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: calls.append(args))

    stats = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=True)

    assert stats.missing_count == 1
    assert stats.dry_run_count == 1
    assert calls == []


def test_incomplete_page_runs_phase_a_b_c_and_no_publish_then_verifies(monkeypatch) -> None:
    calls: list[list[str]] = []
    reads = iter([summary(diary=""), summary()])
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: next(reads))
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: calls.append(args))

    stats = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=False)

    assert stats.incomplete_count == 1
    assert stats.repaired_count == 1
    assert [c[1:] for c in calls] == [
        ["scripts/daily_job.py", "--phase", "ingest", "--target-date", "2026-07-12"],
        ["apps/location_summary_writer/src/main.py", "--target-date", "2026-07-12"],
        ["scripts/daily_job.py", "--phase", "notify_diary", "--target-date", "2026-07-12", "--backfill"],
    ]
    assert all("publish" not in c for c in calls)


@pytest.mark.parametrize("verified", [summary(diary=""), None])
def test_successful_commands_but_verification_not_complete_is_failed(monkeypatch, verified) -> None:
    reads = iter([None, verified])
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: next(reads))
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: None)

    stats = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=False)

    assert stats.repaired_count == 0
    assert stats.failed_count == 1
    assert stats.results[-1].status == "repair_failed"


def test_verification_read_exception_is_failed(monkeypatch) -> None:
    calls = {"n": 0}
    def fake_read(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        raise RuntimeError("readback failed")
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", fake_read)
    monkeypatch.setattr(backfill, "_run_command", lambda args, *, target_date: None)

    stats = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=False)

    assert stats.repaired_count == 0
    assert stats.failed_count == 1


def test_multiple_days_continue_after_failure(monkeypatch) -> None:
    reads = iter([None, summary(), summary(),])
    commands: list[str] = []
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: next(reads))
    def fake_repair(target_date: str) -> None:
        commands.append(target_date)
        if target_date == "2026-07-11":
            raise RuntimeError("phase failed")
    monkeypatch.setattr(backfill, "_repair_day", fake_repair)

    stats = backfill.run_backfill(days=2, end_date="2026-07-12", dry_run=False)

    assert stats.failed_count == 1
    assert stats.complete_count == 1
    assert commands == ["2026-07-11"]


def test_cli_exit_code_one_when_failures(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(backfill, "run_backfill", lambda **kwargs: backfill.BackfillStats(scan_count=1, failed_count=1))
    monkeypatch.setattr(backfill, "parse_args", lambda: SimpleNamespace(days=1, end_date="2026-07-12", dry_run=False, artifact_dir=tmp_path))
    with pytest.raises(SystemExit) as exc:
        backfill.main()
    assert exc.value.code == 1
    assert (tmp_path / "daily_log_repair_result.json").exists()


def test_second_run_complete_page_skips(monkeypatch) -> None:
    monkeypatch.setattr(backfill, "load_config", lambda **kwargs: SimpleNamespace(daily_log_read_url="read", bearer_token="token"))
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: summary())
    monkeypatch.setattr(backfill, "_repair_day", lambda target_date: (_ for _ in ()).throw(AssertionError("should not repair")))

    stats = backfill.run_backfill(days=1, end_date="2026-07-12", dry_run=False)

    assert stats.complete_count == 1
    assert stats.repaired_count == 0
