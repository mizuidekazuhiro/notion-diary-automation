from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import daily_job
from scripts.expense_f_aggregator import aggregate_expense_f_for_dates
from scripts.f_risk_state_store import FRiskStateStore
from scripts.location_for_weather import resolve_location_for_weather, _normalize_geocode_query


def _summary(**kwargs):
    base = {
        "target_date": "2026-03-28",
        "sleep_start": None,
        "sleep_end": None,
        "sleep_duration_min": None,
        "sleep_score": None,
        "place": None,
        "location_summary": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_weather_resolution_prefers_latest_latlon() -> None:
    with patch("scripts.location_for_weather._query_location_log_place", return_value=({"name": "Ueno", "latitude": 35.7, "longitude": 139.7, "resolution_method": "location_log_latest_latlon"}, {"query_status": "ok"})), patch("scripts.location_for_weather._geocode_place") as geocode_mock:
        resolved = resolve_location_for_weather(summary=_summary(place="fallback"))
    assert resolved.resolution_method == "location_log_latest_latlon"
    assert resolved.latitude == 35.7 and resolved.longitude == 139.7
    geocode_mock.assert_not_called()


def test_weather_resolution_uses_location_log_place_geocode_only_when_latlon_missing() -> None:
    with patch("scripts.location_for_weather._query_location_log_place", return_value=({"selected_place": "Tokyo Station"}, {"query_status": "ok", "latlon_available": False})), patch("scripts.location_for_weather._load_geocode_cache", return_value={}), patch("scripts.location_for_weather._geocode_place", return_value=(35.681, 139.767, {"status": "ok"})) as geocode_mock, patch("scripts.location_for_weather._save_geocode_cache"):
        resolved = resolve_location_for_weather(summary=_summary())
    assert resolved.resolution_method == "location_log_latest_place_geocode"
    assert resolved.latitude == 35.681 and resolved.longitude == 139.767
    geocode_mock.assert_called_once()


def test_weather_geocode_query_normalization() -> None:
    assert _normalize_geocode_query("〒100-0001 東京都千代田区, Japan") == "東京都千代田区"


def test_expense_f_date_filter_strategy(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setenv("EXPENSES_DB_ID", "db")
    schema = {"F": {"type": "checkbox"}, "Merchant": {"type": "rich_text"}, "Amount": {"type": "number"}, "Date": {"type": "date"}}

    class Resp:
        status_code = 200

        def json(self):
            return {"results": [], "has_more": False}

    calls = []

    def _post(*args, **kwargs):
        calls.append(kwargs.get("json", {}))
        return Resp()

    with patch("scripts.expense_f_aggregator._fetch_schema", return_value=(schema, {"ok": True})), patch("scripts.expense_f_aggregator.requests.post", side_effect=_post):
        result = aggregate_expense_f_for_dates(["2026-03-28"])["2026-03-28"]
    assert result.data_status == "no_results"
    assert result.debug_summary["filter_strategy"] == "expense_date_prop"
    assert result.debug_summary["resolved_props"]["category"]["resolved_name"] in {None, "Category"}
    assert calls and calls[0]["filter"]["and"][0]["property"] == "F"
    assert calls and calls[0]["filter"]["and"][1]["property"] == "Date"


def test_expense_f_received_at_filter_strategy(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setenv("EXPENSES_DB_ID", "db")
    schema = {"F": {"type": "checkbox"}, "Merchant": {"type": "rich_text"}, "Amount": {"type": "number"}, "Received At": {"type": "date"}}

    class Resp:
        status_code = 200
        def json(self):
            return {"results": [], "has_more": False}
    calls = []
    with patch("scripts.expense_f_aggregator._fetch_schema", return_value=(schema, {"ok": True})), patch("scripts.expense_f_aggregator.requests.post", side_effect=lambda *a, **k: calls.append(k.get("json", {})) or Resp()):
        result = aggregate_expense_f_for_dates(["2026-03-28"])["2026-03-28"]
    assert result.debug_summary["filter_strategy"] == "received_at_prop"
    assert calls and calls[0]["filter"]["and"][1]["property"] == "Received At"


def test_expense_f_query_failed_logs_exception(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setenv("EXPENSES_DB_ID", "db")
    schema = {"F": {}, "Merchant": {}, "Amount": {}}

    with patch("scripts.expense_f_aggregator._fetch_schema", return_value=(schema, {"ok": True})), patch("scripts.expense_f_aggregator.requests.post", side_effect=RuntimeError("boom")):
        result = aggregate_expense_f_for_dates(["2026-03-28"])["2026-03-28"]
    assert result.data_status == "query_failed"
    assert result.debug_summary["query_exception_message"] == "boom"


def test_f_risk_notify_runtime_no_longer_stops_on_backend(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    store = FRiskStateStore()
    assert store.meta.backend == "unavailable"

    cfg = daily_job.Config(
        mail_from="", mail_to=[], gmail_app_password="", tasks_closed_url="", daily_log_upsert_url="", daily_log_ensure_url="", health_ingest_url="", expenses_ingest_url="", daily_log_read_url="", diary_generate_url="", diary_mark_notified_url="", bearer_token=None, openai_model=""
    )
    with patch("scripts.daily_job.generate_f_risk", return_value=SimpleNamespace(alert_text="", score=0.1, reason="ok", matched_patterns=[], skip_reason=None, debug_summary={"risk_json": {"no_alert_reason": "not_matched"}})):
        runtime = daily_job._compute_f_risk_alert_runtime(
            cfg,
            summary=SimpleNamespace(target_date="2026-03-28", resolved_sleep_duration_hours=None, sleep_score=None, weather_code=None, weather_temp_max_c=None, weather_temp_min_c=None, weather_precip_probability_max=None),
            run_id="test",
        )
    assert runtime["state_meta"]["backend"] in {"unavailable", "local_fallback", "github_branch"}
    assert runtime["skip_reason"] is None
