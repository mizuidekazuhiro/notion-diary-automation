from __future__ import annotations

from scripts import notion_readonly_canary as canary


def test_canary_is_read_only_and_uses_supported_expense_filter(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, url: str, *, token: str, payload: dict | None = None) -> dict:
        calls.append((method, url, payload))
        if method == "GET":
            return {"properties": {"Date": {"type": "date"}}}
        if "/expenses/query" in url:
            return {"results": [{"id": "redacted"}]}
        if "/health/query" in url:
            return {
                "results": [
                    {
                        "properties": {
                            "sleep_duration_min": {"type": "number", "number": 374},
                            "sleep_score": {"type": "number", "number": 75},
                            "Protein": {"type": "number", "number": 100},
                            "Kcal": {"type": "number", "number": 2000},
                        }
                    }
                ]
            }
        return {"results": [{"id": "redacted"}]}

    monkeypatch.setattr(canary, "_request", fake_request)
    results = canary.run_canary(
        token="secret",
        expenses_db_id="expenses",
        health_db_id="health",
        daily_log_db_id="daily",
    )

    assert all(method in {"GET", "POST"} for method, _, _ in calls)
    expense_payload = next(payload for method, url, payload in calls if method == "POST" and "/expenses/query" in url)
    assert expense_payload is not None
    assert expense_payload["filter"] == {
        "and": [
            {"property": "F", "checkbox": {"equals": True}},
            {"property": "FamilyCard", "checkbox": {"equals": False}},
        ]
    }
    assert all(result.status == "ok" for result in results)


def test_canary_marks_empty_latest_health_as_no_data(monkeypatch) -> None:
    def fake_request(method: str, url: str, *, token: str, payload: dict | None = None) -> dict:
        if method == "GET":
            return {"properties": {}}
        if "/expenses/query" in url or "/daily/query" in url:
            return {"results": [{"id": "redacted"}]}
        return {"results": [{"properties": {"Date": {"type": "date", "date": {"start": "2026-08-10"}}}}]}

    monkeypatch.setattr(canary, "_request", fake_request)
    results = canary.run_canary(token="secret", expenses_db_id="expenses", health_db_id="health", daily_log_db_id="daily")
    health = next(result for result in results if result.name == "health_latest_quality")
    assert health.status == "no_data"
    assert health.details["available_fields"] == []
