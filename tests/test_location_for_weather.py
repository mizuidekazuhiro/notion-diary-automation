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


def test_resolve_location_prefers_place_label(monkeypatch) -> None:
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
                            "PlaceLabel": {"type": "rich_text", "rich_text": [{"plain_text": "渋谷"}]},
                            "Place": {"type": "rich_text", "rich_text": [{"plain_text": "東京都渋谷区"}]},
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(location_for_weather.requests, "post", fake_post)
    resolved = location_for_weather.resolve_location_for_weather(summary=_summary(), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))

    assert resolved.name == "渋谷"
    assert resolved.resolution_method == "place_label_geocoding"
    assert resolved.debug_summary["location_log_query"]["selected_label"] == "渋谷"


def test_resolve_location_fallbacks_to_place_when_place_label_empty(monkeypatch) -> None:
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
                            "PlaceLabel": {"type": "rich_text", "rich_text": []},
                            "Place": {"type": "rich_text", "rich_text": [{"plain_text": "東京都港区"}]},
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(location_for_weather.requests, "post", fake_post)
    resolved = location_for_weather.resolve_location_for_weather(summary=_summary(), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))

    assert resolved.name == "東京都港区"
    assert resolved.resolution_method == "place_geocoding"


def test_resolve_location_uses_coordinates_without_geocoding(monkeypatch) -> None:
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
                            "PlaceLabel": {"type": "rich_text", "rich_text": [{"plain_text": "新宿"}]},
                            "Place": {"type": "rich_text", "rich_text": [{"plain_text": "東京都新宿区"}]},
                            "Latitude (raw)": {"type": "number", "number": 35.69},
                            "Longitude (raw)": {"type": "number", "number": 139.7},
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(location_for_weather.requests, "post", fake_post)
    resolved = location_for_weather.resolve_location_for_weather(summary=_summary(), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))

    assert resolved.name == "新宿"
    assert resolved.latitude == 35.69
    assert resolved.longitude == 139.7
    assert resolved.resolution_method == "coordinates_direct"


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

    assert resolved.name is None
    payload = seen_payloads[0]
    assert payload["filter"]["and"][0]["property"] == "Time"
    assert payload["sorts"][0]["property"] == "Time"
    assert resolved.debug_summary["location_log_query"]["used_property_names"] == {
        "time": "Time",
        "place_label": "PlaceLabel",
        "place": "Place",
        "latitude": "Latitude (raw)",
        "longitude": "Longitude (raw)",
    }


def test_invalid_coordinates_fallbacks_to_place_geocoding(monkeypatch) -> None:
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
                            "PlaceLabel": {"type": "rich_text", "rich_text": []},
                            "Place": {"type": "rich_text", "rich_text": [{"plain_text": "東京都中央区"}]},
                            "Latitude (raw)": {"type": "number", "number": 999},
                            "Longitude (raw)": {"type": "number", "number": 139.7},
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(location_for_weather.requests, "post", fake_post)
    resolved = location_for_weather.resolve_location_for_weather(summary=_summary(), now=datetime.fromisoformat("2026-03-20T12:00:00+09:00"))

    assert resolved.name == "東京都中央区"
    assert resolved.latitude is None
    assert resolved.longitude is None
    assert resolved.resolution_method == "place_geocoding"
    assert resolved.debug_summary["location_log_query"]["invalid_coordinates"] == ["latitude"]
