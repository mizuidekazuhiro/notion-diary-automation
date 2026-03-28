from __future__ import annotations

from scripts import weather_client


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def test_fetch_weather_for_date_bypasses_geocode_when_coordinates_exist(monkeypatch) -> None:
    called_urls: list[str] = []

    def fake_get(url, params, timeout):
        del timeout
        called_urls.append(url)
        if "forecast" in url:
            assert params["latitude"] == 35.0
            assert params["longitude"] == 139.0
            return _Resp(
                200,
                {
                    "daily": {
                        "weathercode": [0],
                        "temperature_2m_max": [25],
                        "temperature_2m_min": [15],
                        "precipitation_probability_max": [10],
                    }
                },
            )
        raise AssertionError("geocoding should not be called")

    monkeypatch.setattr(weather_client.requests, "get", fake_get)
    result = weather_client.fetch_weather_for_date(
        location_label="東京",
        target_date="2026-03-20",
        latitude=35.0,
        longitude=139.0,
    )

    assert result.available is True
    assert result.location_label == "東京"
    assert called_urls == ["https://api.open-meteo.com/v1/forecast"]
    assert result.debug_summary["used_geocoding"] is False
