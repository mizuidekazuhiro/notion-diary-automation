from __future__ import annotations

import pytest
import requests

from ingest import http_client


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object], headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = str(payload)

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            exc = requests.exceptions.HTTPError(f"status={self.status_code}")
            exc.response = self  # type: ignore[attr-defined]
            raise exc


def test_fetch_json_retries_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    class _Session:
        def get(self, url: str, headers: dict[str, str], timeout: object) -> _Response:
            del url, headers, timeout
            attempts["count"] += 1
            if attempts["count"] == 1:
                return _Response(429, {"error": "rate_limited"}, headers={"Retry-After": "0"})
            return _Response(200, {"ok": True})

    monkeypatch.setattr(http_client, "FETCH_RETRY_MAX_RETRIES", 5)
    monkeypatch.setattr(http_client, "_session", lambda: _Session())
    monkeypatch.setattr(http_client.time, "sleep", lambda sec: sleeps.append(sec))

    payload = http_client.fetch_json("https://example.com/api/daily_log?date=2026-02-12", bearer_token=None)

    assert payload == {"ok": True}
    assert attempts["count"] == 2
    assert sleeps == [0.0]


def test_fetch_json_retries_429_until_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    class _Session:
        def get(self, url: str, headers: dict[str, str], timeout: object) -> _Response:
            del url, headers, timeout
            attempts["count"] += 1
            return _Response(429, {"error": "rate_limited"}, headers={"Retry-After": "0"})

    monkeypatch.setattr(http_client, "FETCH_RETRY_MAX_RETRIES", 2)
    monkeypatch.setattr(http_client, "_session", lambda: _Session())
    monkeypatch.setattr(http_client.time, "sleep", lambda sec: sleeps.append(sec))

    with pytest.raises(RuntimeError):
        http_client.fetch_json("https://example.com/api/daily_log?date=2026-02-12", bearer_token=None)

    assert attempts["count"] == 3
    assert sleeps == [0.0, 0.0]
