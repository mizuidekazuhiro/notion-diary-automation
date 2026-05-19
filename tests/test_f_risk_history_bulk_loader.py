from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import f_risk_generator


def test_bulk_history_success_does_not_call_read_daily_log_for_every_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        f_risk_generator,
        "fetch_json",
        lambda url, token: {
            "items": [
                {"target_date": "2026-03-10", "page_id": "p1", "title": "d1"},
                {"target_date": "2026-03-09", "page_id": "p2", "title": "d2"},
            ]
        },
    )
    calls = {"count": 0}
    monkeypatch.setattr(
        f_risk_generator,
        "read_daily_log",
        lambda **kwargs: calls.__setitem__("count", calls["count"] + 1) or None,
    )
    out = f_risk_generator._load_histories_with_bulk_fallback(
        daily_log_read_url="https://example.com/api/daily_log",
        bearer_token=None,
        target_date="2026-03-10",
        days=365,
    )
    assert len(out) == 2
    assert calls["count"] == 0


def test_bulk_history_failure_falls_back_to_single_day_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f_risk_generator, "fetch_json", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(
        f_risk_generator,
        "_load_histories",
        lambda **kwargs: [SimpleNamespace(target_date="2026-03-10")],
    )
    out = f_risk_generator._load_histories_with_bulk_fallback(
        daily_log_read_url="https://example.com/api/daily_log",
        bearer_token=None,
        target_date="2026-03-10",
        days=30,
    )
    assert len(out) == 1
