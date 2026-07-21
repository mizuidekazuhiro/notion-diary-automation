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
        activity_summary="summary",
        today_advice="advice",
        diary="diary",
        today_advice_generated_at="2026-07-12T07:00:00+09:00",
        diary_generated_at="2026-07-12T07:05:00+09:00",
        page_id="page-1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_generate_target_dates_jst_example() -> None:
    assert (
        backfill.default_end_date(
            datetime(2026, 7, 20, 12, 7, tzinfo=ZoneInfo("Asia/Tokyo"))
        )
        == "2026-07-19"
    )
    assert backfill.generate_target_dates(end_date="2026-07-19", days=7) == [
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
        "2026-07-18",
        "2026-07-19",
    ]


def test_classification_missing_incomplete_complete() -> None:
    assert backfill.classify_daily_log(None)[0] == "missing"
    assert backfill.classify_daily_log(summary())[0] == "complete"
    assert backfill.classify_daily_log(summary(diary=""))[0] == "incomplete"


def test_diary_generated_at_missing_is_incomplete() -> None:
    classification, missing = backfill.classify_daily_log(
        summary(diary_generated_at="")
    )
    assert classification == "incomplete"
    assert "diary_generated_at" in missing


def test_activity_summary_empty_can_still_be_complete() -> None:
    classification, missing = backfill.classify_daily_log(
        summary(activity_summary="", notes="", expenses=[])
    )
    assert classification == "complete"
    assert missing == []


def test_dry_run_missing_does_not_run_phases(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        backfill,
        "load_config",
        lambda **kwargs: SimpleNamespace(
            daily_log_read_url="read", bearer_token="token"
        ),
    )
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: None)
    monkeypatch.setattr(
        backfill, "_run_command", lambda args, *, target_date: calls.append(args)
    )

    stats = backfill.run_backfill(
        days=1,
        end_date="2026-07-12",
        dry_run=True,
        send_mail=True,
    )

    assert stats.missing_count == 1
    assert stats.dry_run_count == 1
    assert stats.results[-1].mail_status == "would_process"
    assert calls == []


def test_incomplete_page_runs_phase_a_b_c_and_no_publish_then_verifies(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []
    reads = iter([summary(diary=""), summary()])
    monkeypatch.setattr(
        backfill,
        "load_config",
        lambda **kwargs: SimpleNamespace(
            daily_log_read_url="read", bearer_token="token"
        ),
    )
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: next(reads))
    monkeypatch.setattr(
        backfill, "_run_command", lambda args, *, target_date: calls.append(args)
    )

    stats = backfill.run_backfill(
        days=1,
        end_date="2026-07-12",
        dry_run=False,
    )

    assert stats.incomplete_count == 1
    assert stats.repaired_count == 1
    assert [command[1:] for command in calls] == [
        [
            "scripts/daily_job.py",
            "--phase",
            "ingest",
            "--target-date",
            "2026-07-12",
        ],
        [
            "apps/location_summary_writer/src/main.py",
            "--target-date",
            "2026-07-12",
        ],
        [
            "scripts/daily_job.py",
            "--phase",
            "notify_diary",
            "--target-date",
            "2026-07-12",
            "--backfill",
        ],
    ]
    assert all("publish" not in command for command in calls)


def test_complete_page_processes_historical_mail_when_requested(monkeypatch) -> None:
    published: list[str] = []
    monkeypatch.setattr(
        backfill,
        "load_config",
        lambda **kwargs: SimpleNamespace(
            daily_log_read_url="read", bearer_token="token"
        ),
    )
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: summary())
    monkeypatch.setattr(
        backfill, "_publish_day", lambda target_date: published.append(target_date)
    )
    monkeypatch.setattr(
        backfill,
        "_repair_day",
        lambda target_date: (_ for _ in ()).throw(
            AssertionError("complete page should not be repaired")
        ),
    )

    stats = backfill.run_backfill(
        days=1,
        end_date="2026-07-12",
        dry_run=False,
        send_mail=True,
    )

    assert published == ["2026-07-12"]
    assert stats.complete_count == 1
    assert stats.mail_processed_count == 1
    assert stats.results[-1].status == "complete_mail_processed"
    assert stats.results[-1].mail_status == "processed"


def test_repaired_page_processes_mail_after_verification(monkeypatch) -> None:
    calls: list[list[str]] = []
    reads = iter([summary(diary=""), summary()])
    monkeypatch.setattr(
        backfill,
        "load_config",
        lambda **kwargs: SimpleNamespace(
            daily_log_read_url="read", bearer_token="token"
        ),
    )
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: next(reads))
    monkeypatch.setattr(
        backfill, "_run_command", lambda args, *, target_date: calls.append(args)
    )

    stats = backfill.run_backfill(
        days=1,
        end_date="2026-07-12",
        dry_run=False,
        send_mail=True,
    )

    assert [command[1:] for command in calls][-1] == [
        "scripts/daily_job.py",
        "--phase",
        "publish",
        "--target-date",
        "2026-07-12",
    ]
    assert stats.repaired_count == 1
    assert stats.mail_processed_count == 1
    assert stats.results[-1].status == "repair_success_mail_processed"


@pytest.mark.parametrize("verified", [summary(diary=""), None])
def test_successful_commands_but_verification_not_complete_is_failed(
    monkeypatch, verified
) -> None:
    reads = iter([None, verified])
    monkeypatch.setattr(
        backfill,
        "load_config",
        lambda **kwargs: SimpleNamespace(
            daily_log_read_url="read", bearer_token="token"
        ),
    )
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: next(reads))
    monkeypatch.setattr(
        backfill, "_run_command", lambda args, *, target_date: None
    )

    stats = backfill.run_backfill(
        days=1,
        end_date="2026-07-12",
        dry_run=False,
    )

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

    monkeypatch.setattr(
        backfill,
        "load_config",
        lambda **kwargs: SimpleNamespace(
            daily_log_read_url="read", bearer_token="token"
        ),
    )
    monkeypatch.setattr(backfill, "read_daily_log", fake_read)
    monkeypatch.setattr(
        backfill, "_run_command", lambda args, *, target_date: None
    )

    stats = backfill.run_backfill(
        days=1,
        end_date="2026-07-12",
        dry_run=False,
    )

    assert stats.repaired_count == 0
    assert stats.failed_count == 1


def test_multiple_days_continue_after_failure(monkeypatch) -> None:
    reads = iter([None, summary(), summary()])
    commands: list[str] = []
    monkeypatch.setattr(
        backfill,
        "load_config",
        lambda **kwargs: SimpleNamespace(
            daily_log_read_url="read", bearer_token="token"
        ),
    )
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: next(reads))

    def fake_repair(target_date: str) -> None:
        commands.append(target_date)
        if target_date == "2026-07-11":
            raise RuntimeError("phase failed")

    monkeypatch.setattr(backfill, "_repair_day", fake_repair)

    stats = backfill.run_backfill(
        days=2,
        end_date="2026-07-12",
        dry_run=False,
    )

    assert stats.failed_count == 1
    assert stats.complete_count == 1
    assert commands == ["2026-07-11"]


def test_cli_exit_code_one_when_failures(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        backfill,
        "run_backfill",
        lambda **kwargs: backfill.BackfillStats(scan_count=1, failed_count=1),
    )
    monkeypatch.setattr(
        backfill,
        "parse_args",
        lambda: SimpleNamespace(
            days=1,
            end_date="2026-07-12",
            dry_run=False,
            send_mail=False,
            artifact_dir=tmp_path,
        ),
    )
    with pytest.raises(SystemExit) as exc:
        backfill.main()
    assert exc.value.code == 1
    assert (tmp_path / "daily_log_repair_result.json").exists()


def test_second_run_complete_page_skips_without_mail_request(monkeypatch) -> None:
    monkeypatch.setattr(
        backfill,
        "load_config",
        lambda **kwargs: SimpleNamespace(
            daily_log_read_url="read", bearer_token="token"
        ),
    )
    monkeypatch.setattr(backfill, "read_daily_log", lambda **kwargs: summary())
    monkeypatch.setattr(
        backfill,
        "_repair_day",
        lambda target_date: (_ for _ in ()).throw(
            AssertionError("should not repair")
        ),
    )
    monkeypatch.setattr(
        backfill,
        "_publish_day",
        lambda target_date: (_ for _ in ()).throw(
            AssertionError("should not publish")
        ),
    )

    stats = backfill.run_backfill(
        days=1,
        end_date="2026-07-12",
        dry_run=False,
    )

    assert stats.complete_count == 1
    assert stats.repaired_count == 0
    assert stats.mail_processed_count == 0
