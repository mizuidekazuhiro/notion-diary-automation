from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from delivery.email_sender import build_email_message
from scripts.weekly_report import (
    _diff,
    _metrics,
    compute_weekly_window,
    get_weekly_send_hour_jst,
    should_send_now,
)


class DummySummary:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _row(date: str, **overrides):
    base = {
        "target_date": date,
        "resolved_sleep_duration_min": 420,
        "sleep_score": 80,
        "mood": "★★★",
        "expenses_total": 1000,
        "done_count": 3,
        "drop_count": 1,
        "weight": 70.0,
        "expense_f_count": 0,
        "notes": "",
        "diary": "",
    }
    base.update(overrides)
    return DummySummary(**base)


def test_weekly_window_is_previous_monday_0500_to_sunday_045959() -> None:
    now = datetime(2026, 3, 29, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    w = compute_weekly_window(now=now)
    assert w.start.isoformat() == "2026-03-23T05:00:00+09:00"
    assert w.end.isoformat() == "2026-03-29T04:59:59+09:00"


def test_previous_diff_is_calculated() -> None:
    assert _diff(10.5, 9.0) == 1.5


def test_weight_missing_not_filled_and_insufficient_under_3_days() -> None:
    rows = [_row("2026-03-23", weight=70.1), _row("2026-03-24", weight=None), _row("2026-03-25", weight=70.0)]
    m = _metrics(rows)
    assert m["weight"] == [70.1, None, 70.0]
    assert m["weight_insufficient"] is True


def test_f_alert_not_present_when_no_f_days() -> None:
    rows = [_row("2026-03-23"), _row("2026-03-24")]
    m = _metrics(rows)
    assert m["f_days"] == 0


def test_cc_bcc_optional_for_message_payload() -> None:
    msg = build_email_message(
        mail_from="from@example.com",
        mail_to=["to@example.com"],
        subject="x",
        plain_text="p",
        html_body="<p>h</p>",
    )
    assert msg["To"] == "to@example.com"


def test_default_send_hour_is_21(monkeypatch) -> None:
    monkeypatch.delenv("WEEKLY_REPORT_SEND_HOUR_JST", raising=False)
    assert get_weekly_send_hour_jst() == 21


def test_weekly_disabled_skips(monkeypatch) -> None:
    monkeypatch.delenv("WEEKLY_REPORT_ENABLED", raising=False)
    ok, reason = should_send_now(now=datetime(2026, 3, 29, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo")))
    assert ok is False
    assert reason == "weekly_disabled_by_env"
