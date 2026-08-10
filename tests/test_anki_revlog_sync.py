from __future__ import annotations

from datetime import date, datetime
import sqlite3
from urllib.error import URLError

import pytest

from scripts import anki_revlog_sync as sync


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def test_jst_0400_boundary_is_explicit_and_dst_independent() -> None:
    before = _ms("2026-08-10T03:59:59+09:00")
    boundary = _ms("2026-08-10T04:00:00+09:00")
    assert sync.resolve_target_date(before) == date(2026, 8, 9)
    assert sync.resolve_target_date(boundary) == date(2026, 8, 10)
    start, end = sync.study_window(date(2026, 8, 10))
    assert start.isoformat() == "2026-08-10T04:00:00+09:00"
    assert end.isoformat() == "2026-08-11T04:00:00+09:00"


def test_revlog_time_sum_ten_minute_sessions_and_max_count() -> None:
    reviews = [
        sync.Review(_ms("2026-08-10T04:00:00+09:00"), 60_000),
        sync.Review(_ms("2026-08-10T04:09:59+09:00"), 300_000),
        sync.Review(_ms("2026-08-10T04:19:59+09:00"), 241_000),
        sync.Review(_ms("2026-08-11T04:00:00+09:00"), 999_000),
    ]
    result = sync.aggregate_reviews(reviews, date(2026, 8, 10))
    assert result.study_minutes == 10.02
    assert result.study_sessions == 2
    assert result.review_count == 3
    assert result.max_time_review_count == 1
    assert result.first_review_at == "2026-08-10T04:00:00.000+09:00"
    assert result.last_review_at == "2026-08-10T04:19:59.000+09:00"


def test_empty_day_payload_is_safe_for_authoritative_zero() -> None:
    result = sync.aggregate_reviews([], date(2026, 8, 10))
    assert result.to_payload() == {
        "target_date": "2026-08-10",
        "study_minutes": 0.0,
        "study_sessions": 0,
        "first_review_at": None,
        "last_review_at": None,
        "review_count": 0,
        "max_time_review_count": 0,
        "source": "anki_revlog",
    }


def test_backfill_dates_include_current_study_day() -> None:
    now = datetime.fromisoformat("2026-08-11T03:00:00+09:00")
    assert sync.target_dates_for_backfill(now, 3) == [
        date(2026, 8, 8),
        date(2026, 8, 9),
        date(2026, 8, 10),
    ]


def test_card_review_rows_are_deduplicated_across_parent_and_child_decks() -> None:
    rows = [
        [1000, 1, -1, 3, 1, 1, 2500, 2000, 1],
        [1000, 1, -1, 3, 1, 1, 2500, 2000, 1],
        [2000, 2, -1, 4, 1, 1, 2500, 3000, 1],
    ]
    assert sync.parse_card_reviews(rows) == [
        sync.Review(1000, 2000),
        sync.Review(2000, 3000),
    ]


def test_worker_url_accepts_existing_daily_log_endpoint() -> None:
    assert sync.normalize_worker_url(
        "https://example.workers.dev/execute/api/daily_log/upsert"
    ) == "https://example.workers.dev/execute/api/study/anki-daily"


def test_ankiconnect_stopped_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(sync, "urlopen", fail)
    with pytest.raises(sync.SyncError, match="Start Anki"):
        sync.AnkiConnectClient().check()


def test_sqlite_backup_reads_wal_consistently(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    collection = tmp_path / "Anki2" / "Profile" / "collection.anki2"
    collection.parent.mkdir(parents=True)
    writer = sqlite3.connect(collection)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE revlog (id INTEGER PRIMARY KEY, time INTEGER NOT NULL)")
    writer.execute("INSERT INTO revlog VALUES (?, ?)", (1000, 2000))
    writer.commit()
    try:
        assert sync.collect_reviews_via_sqlite_backup(
            profile="Profile", start_ms=0, end_ms=2000
        ) == [sync.Review(1000, 2000)]
    finally:
        writer.close()


def test_sqlite_failure_is_not_silently_treated_as_zero(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    with pytest.raises(sync.SyncError, match="collection was not found"):
        sync.collect_reviews_via_sqlite_backup(
            profile="Missing", start_ms=0, end_ms=2000
        )
