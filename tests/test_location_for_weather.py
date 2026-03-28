from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from scripts import location_for_weather


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def _summary(target_date: str = "2026-03-20") -> SimpleNamespace:
    return SimpleNamespace(target_date=target_date)


def test_resolve_location_prefers_location_log_place(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("LOCATION_LOG_DB_ID", "db")

    def fake_post(url, headers, json, timeout):
        del url, headers, timeout
        return _Resp(
            200,
            {
                "results": [
                    {
                        "id": "row-1",
                        "properties": {
                            "Time": {"type": "date", "date": {"start": "2026-03-20T08:00:00+09:00"}},
                            "Place": {"type": "rich_text", "rich_text": [{"plain_text": "東京都渋谷区"}]},
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(location_for_weather.requests, "post", fake_post)
    resolved = location_for_weather.resolve_location_for_weather(summary=_summary(), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))

    assert resolved.name == "東京都渋谷区"
    assert resolved.resolution_method == "place_geocoding"
    assert resolved.debug_summary["location_log_query"]["selected_label"] == "東京都渋谷区"
    assert resolved.debug_summary["location_log_query"]["query_status"] == "ok"


def test_resolve_location_fallbacks_to_daily_log_place_when_location_log_empty(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("LOCATION_LOG_DB_ID", "db")

    def fake_post(url, headers, json, timeout):
        del url, headers, timeout
        return _Resp(200, {"results": []})

    monkeypatch.setattr(location_for_weather.requests, "post", fake_post)
    resolved = location_for_weather.resolve_location_for_weather(summary=SimpleNamespace(target_date="2026-03-20", place="東京都港区"), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))

    assert resolved.name == "東京都港区"
    assert resolved.source == "daily_log_place"


def test_resolve_location_fallback_chain_to_location_summary_and_tokyo(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("LOCATION_LOG_DB_ID", "db")

    def fake_post(url, headers, json, timeout):
        del url, headers, timeout
        return _Resp(200, {"results": []})

    monkeypatch.setattr(location_for_weather.requests, "post", fake_post)
    resolved = location_for_weather.resolve_location_for_weather(
        summary=SimpleNamespace(target_date="2026-03-20", place="", location_summary="横浜"),
        now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"),
    )
    assert resolved.name == "横浜"
    assert resolved.source == "daily_log_location_summary"
    resolved2 = location_for_weather.resolve_location_for_weather(
        summary=SimpleNamespace(target_date="2026-03-20", place="", location_summary=""),
        now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"),
    )
    assert resolved2.name == "東京都"
    assert resolved2.source == "fallback_default_tokyo"


def test_missing_notion_env_and_default_properties(monkeypatch) -> None:
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("LOCATION_LOG_DB_ID", raising=False)
    resolved = location_for_weather.resolve_location_for_weather(summary=SimpleNamespace(target_date="2026-03-20", place="", location_summary=""), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))
    assert resolved.name == "東京都"
    assert resolved.debug_summary["location_log_query"]["query_status"] == "missing_notion_env"
    assert resolved.debug_summary["location_log_query"]["notion_token_present"] is False


def test_default_properties_match_location_log_schema(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("LOCATION_LOG_DB_ID", "db")
    for key in [
        "LOCATION_LOG_TIME_PROP",
        "LOCATION_LOG_PLACE_LABEL_PROP",
        "LOCATION_LOG_PLACE_PROP",
        "LOCATION_LOG_LATITUDE_PROP",
        "LOCATION_LOG_LONGITUDE_PROP",
    ]:
        monkeypatch.delenv(key, raising=False)

    seen_payloads: list[dict[str, object]] = []

    def fake_post(url, headers, json, timeout):
        del url, headers, timeout
        seen_payloads.append(json)
        return _Resp(200, {"results": []})

    monkeypatch.setattr(location_for_weather.requests, "post", fake_post)
    resolved = location_for_weather.resolve_location_for_weather(summary=_summary(), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))

    assert resolved.name == "東京都"
    payload = seen_payloads[0]
    assert payload["filter"]["and"][0]["property"] == "Time"
    assert payload["sorts"][0]["property"] == "Time"
    assert resolved.debug_summary["location_log_query"]["effective_time_prop"] == "Time"


def test_location_log_status_distinguishes_errors(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "token")
    monkeypatch.setenv("LOCATION_LOG_DB_ID", "db")
    monkeypatch.setattr(location_for_weather.requests, "post", lambda *args, **kwargs: _Resp(500, {}))
    resolved = location_for_weather.resolve_location_for_weather(summary=SimpleNamespace(target_date="2026-03-20", place="", location_summary=""), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))
    assert resolved.debug_summary["location_log_query"]["query_status"] == "notion_error"
