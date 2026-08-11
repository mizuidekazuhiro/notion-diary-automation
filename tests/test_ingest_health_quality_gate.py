from __future__ import annotations

from types import SimpleNamespace

import pytest

import ingest.ingest_sources as ingest_module


class _FakeConnector:
    def __init__(self, connector_id: str, result: object, calls: list[str]) -> None:
        self.id = connector_id
        self._result = result
        self._calls = calls

    def fetch(self, target_date: str) -> object:
        self._calls.append(self.id)
        return self._result

    def render(self, result: object) -> dict[str, object]:
        return {"summary_blocks": {}, "raw_payload": {"id": self.id}}


def test_phase_a_records_other_sources_then_fails_for_health_transport_failure(monkeypatch):
    calls: list[str] = []
    health_result = SimpleNamespace(
        status="failed",
        error_code="connector_exception",
        payload={},
    )
    monkeypatch.setattr(
        ingest_module,
        "TasksConnector",
        lambda *args: _FakeConnector("tasks", SimpleNamespace(payload={}), calls),
    )
    monkeypatch.setattr(
        ingest_module,
        "HealthConnector",
        lambda *args: _FakeConnector("health", health_result, calls),
    )
    monkeypatch.setattr(
        ingest_module,
        "ExpensesConnector",
        lambda *args: _FakeConnector("expenses", SimpleNamespace(payload={}), calls),
    )
    upserts: list[dict[str, object]] = []
    monkeypatch.setattr(
        ingest_module,
        "upsert_daily_log",
        lambda url, payload, token: upserts.append(payload) or {"ok": True},
    )

    with pytest.raises(ingest_module.HealthDataQualityError, match="connector_exception"):
        ingest_module.ingest_sources(
            target_date="2026-08-10",
            page_id="daily-page",
            tasks_closed_url="https://example.test/tasks",
            health_ingest_url="https://example.test/health",
            expenses_ingest_url="https://example.test/expenses",
            daily_log_upsert_url="https://example.test/daily",
            bearer_token="secret",
            run_id="run",
            source_label="automation",
        )

    assert calls == ["tasks", "health", "expenses"]
    assert len(upserts) == 1


@pytest.mark.parametrize("status", ["ok", "degraded", "no_data", "stale"])
def test_phase_a_does_not_stop_for_health_data_quality_warning(status, monkeypatch):
    calls: list[str] = []
    health_result = SimpleNamespace(status=status, error_code=None, payload={"ok": True})
    monkeypatch.setattr(
        ingest_module,
        "TasksConnector",
        lambda *args: _FakeConnector("tasks", SimpleNamespace(payload={}), calls),
    )
    monkeypatch.setattr(
        ingest_module,
        "HealthConnector",
        lambda *args: _FakeConnector("health", health_result, calls),
    )
    monkeypatch.setattr(
        ingest_module,
        "ExpensesConnector",
        lambda *args: _FakeConnector("expenses", SimpleNamespace(payload={}), calls),
    )
    monkeypatch.setattr(
        ingest_module,
        "upsert_daily_log",
        lambda *args: {"ok": True},
    )

    result = ingest_module.ingest_sources(
        target_date="2026-08-10",
        page_id="daily-page",
        tasks_closed_url="https://example.test/tasks",
        health_ingest_url="https://example.test/health",
        expenses_ingest_url="https://example.test/expenses",
        daily_log_upsert_url="https://example.test/daily",
        bearer_token="secret",
        run_id="run",
        source_label="automation",
    )

    assert result.sources == ["tasks", "health", "expenses"]
