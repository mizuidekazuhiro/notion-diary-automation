from connectors.health import HealthConnector


def test_empty_health_page_is_no_data(monkeypatch):
    monkeypatch.setattr("connectors.health.post_json", lambda *args, **kwargs: {"ok": True, "target_date": "2026-08-10", "found": True})
    result = HealthConnector("https://example.test", None).fetch("2026-08-10")
    assert result.ok is False
    assert result.status == "no_data"
    assert result.completeness == 0
    assert result.error_code == "major_fields_empty"


def test_partial_health_page_is_degraded(monkeypatch):
    monkeypatch.setattr("connectors.health.post_json", lambda *args, **kwargs: {"ok": True, "target_date": "2026-08-10", "sleep_duration_min": 420})
    result = HealthConnector("https://example.test", None).fetch("2026-08-10")
    assert result.status == "degraded"
    assert result.available_fields == ("sleep_duration_min",)


def test_health_quality_contract_from_worker(monkeypatch):
    monkeypatch.setattr(
        "connectors.health.post_json",
        lambda *args, **kwargs: {
            "ok": True,
            "target_date": "2026-08-10",
            "health_quality": {
                "status": "ok",
                "data_date": "2026-08-10",
                "last_valid_at": "2026-08-11T00:00:00Z",
                "completeness": 0.75,
                "available_fields": ["sleep_duration_min", "sleep_score", "readiness_hrv", "readiness_bpm", "kcal", "protein"],
                "error_code": None,
            },
        },
    )
    result = HealthConnector("https://example.test", None).fetch("2026-08-10")
    assert result.ok is True
    assert result.data_date == "2026-08-10"
    assert result.last_valid_at == "2026-08-11T00:00:00Z"
