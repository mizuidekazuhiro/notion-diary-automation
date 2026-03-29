from __future__ import annotations

from scripts import weather_client


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, object]:
        return self._payload


def test_fetch_weather_for_date_bypasses_geocode_when_coordinates_exist(monkeypatch) -> None:
    called_urls: list[str] = []

    def fake_get(url, params, timeout):
        del timeout
        called_urls.append(url)
        if "jma" in url:
            assert params["latitude"] == 35.0
            assert params["longitude"] == 139.0
            return _Resp(
                200,
                {
                    "daily": {
                        "weather_code": [0],
                        "temperature_2m_max": [25],
                        "temperature_2m_min": [15],
                        "precipitation_sum": [0.0],
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
    assert result.summary == "晴れ。最高25.0℃、最低15.0℃、降水量0.0mmです。"
    assert called_urls == ["https://api.open-meteo.com/v1/jma"]
    assert result.debug_summary["used_geocoding"] is False


def test_build_weather_summary_with_precipitation_sum() -> None:
    summary = weather_client.build_weather_summary(
        weather_code=None,
        temp_max_c=17.4,
        temp_min_c=None,
        precip_probability_max=None,
        precipitation_sum_mm=3.2,
    )
    assert summary == "最高17.4℃、降水量3.2mmです。"


def test_build_weather_summary_japanese_sentence_from_raw_values_with_precipitation_sum() -> None:
    summary = weather_client.build_weather_summary(
        weather_code=61,
        temp_max_c=17.4,
        temp_min_c=8.9,
        precip_probability_max=None,
        precipitation_sum_mm=14.5,
    )
    assert summary == "弱い雨。最高17.4℃、最低8.9℃、降水量14.5mmです。"


def test_build_weather_summary_omits_precipitation_sentence_when_missing() -> None:
    summary = weather_client.build_weather_summary(
        weather_code=3,
        temp_max_c=10.0,
        temp_min_c=5.0,
        precip_probability_max=None,
        precipitation_sum_mm=None,
    )
    assert summary == "くもり。最高10.0℃、最低5.0℃です。"


def test_fetch_weather_for_date_without_precipitation_sum(monkeypatch) -> None:
    def fake_get(url, params, timeout):
        del timeout
        assert "jma" in url
        assert params["timezone"] == "Asia/Tokyo"
        assert params["start_date"] == "2026-03-20"
        assert params["end_date"] == "2026-03-20"
        return _Resp(
            200,
            {
                "daily": {
                    "weather_code": [3],
                    "temperature_2m_max": [18],
                    "temperature_2m_min": [10],
                }
            },
        )

    monkeypatch.setattr(weather_client.requests, "get", fake_get)
    result = weather_client.fetch_weather_for_date(
        location_label="東京",
        target_date="2026-03-20",
        latitude=35.0,
        longitude=139.0,
    )

    assert result.available is True
    assert result.precipitation_sum_mm is None
    assert result.summary == "くもり。最高18.0℃、最低10.0℃です。"


def test_build_weather_select_label_from_weather_code() -> None:
    assert weather_client.build_weather_select_label(61) == "雨"
    assert weather_client.build_weather_select_label(0) == "晴れ"
    assert weather_client.build_weather_select_label(95) == "雷雨"


def test_build_weather_select_label_fallbacks_to_summary_text() -> None:
    assert weather_client.build_weather_select_label(999, "雪。最高2.0℃、最低-1.0℃です。") == "雪"
    assert weather_client.build_weather_select_label(None, "霧。視界が悪いです。") == "霧"
    assert weather_client.build_weather_select_label(None, "天気情報なし") is None
