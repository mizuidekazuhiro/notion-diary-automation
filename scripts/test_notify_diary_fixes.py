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
from scripts.location_for_weather import resolve_location_for_weather
from scripts.note_batch_labeler import parse_note_label_json_with_meta
from scripts.sleep_utils import resolve_sleep_for_target_date


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


def test_weather_resolution_prefers_latlon_and_skips_geocode() -> None:
    with patch("scripts.location_for_weather._query_location_log_place", return_value=({"name": "Ueno", "latitude": 35.7, "longitude": 139.7, "resolution_method": "latlon_direct"}, {})), patch("scripts.location_for_weather._geocode_place") as geocode_mock:
        resolved = resolve_location_for_weather(summary=_summary(place="fallback"))
    assert resolved.resolution_method == "latlon_direct"
    assert resolved.latitude == 35.7 and resolved.longitude == 139.7
    geocode_mock.assert_not_called()


def test_weather_resolution_uses_cache_before_geocode() -> None:
    with patch("scripts.location_for_weather._query_location_log_place", return_value=({"name": "Tokyo Station", "resolution_method": "pending_geocode"}, {})), patch("scripts.location_for_weather._load_geocode_cache", return_value={"tokyo station": {"lat": 35.681, "lon": 139.767}}), patch("scripts.location_for_weather._geocode_place") as geocode_mock:
        resolved = resolve_location_for_weather(summary=_summary())
    assert resolved.resolution_method == "geocode_cache"
    assert resolved.latitude == 35.681 and resolved.longitude == 139.767
    geocode_mock.assert_not_called()


def test_weather_resolution_calls_geocode_only_when_needed() -> None:
    with patch("scripts.location_for_weather._query_location_log_place", return_value=({"name": "Shibuya", "resolution_method": "pending_geocode"}, {})), patch("scripts.location_for_weather._load_geocode_cache", return_value={}), patch("scripts.location_for_weather._geocode_place", return_value=(35.66, 139.7, {"status": "ok"})) as geocode_mock, patch("scripts.location_for_weather._save_geocode_cache"):
        resolved = resolve_location_for_weather(summary=_summary())
    assert resolved.resolution_method == "place_geocoding"
    assert resolved.latitude == 35.66 and resolved.longitude == 139.7
    geocode_mock.assert_called_once()


def test_expense_f_no_category_still_resolved(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setenv("EXPENSES_DB_ID", "db")
    schema = {
        "F": {},
        "Date": {},
        "Merchant": {},
        "Amount": {},
    }

    class Resp:
        status_code = 200

        def json(self):
            return {"results": [], "has_more": False}

    with patch("scripts.expense_f_aggregator._fetch_schema", return_value=(schema, {"ok": True})), patch("scripts.expense_f_aggregator.requests.post", return_value=Resp()):
        result = aggregate_expense_f_for_dates(["2026-03-28"])["2026-03-28"]
    assert result.data_status != "schema_unresolved"


def test_expense_f_missing_required_is_schema_unresolved(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setenv("EXPENSES_DB_ID", "db")
    schema = {"F": {}, "Date": {}, "Amount": {}}
    with patch("scripts.expense_f_aggregator._fetch_schema", return_value=(schema, {"ok": True})):
        result = aggregate_expense_f_for_dates(["2026-03-28"])["2026-03-28"]
    assert result.data_status == "schema_unresolved"


def test_sleep_common_resolver_target_date_and_unavailable() -> None:
    today = _summary(target_date="2026-03-28")
    history = [_summary(target_date="2026-03-27", sleep_start="2026-03-28T04:30:00+09:00", sleep_end="2026-03-28T10:30:00+09:00", sleep_duration_min=360, sleep_score=74)]
    candidates, selected, mode = resolve_sleep_for_target_date(target_date="2026-03-27", today_summary=today, history_summaries=history)
    assert mode == "history_target_date_match"
    assert selected is not None
    assert selected["candidate_target_date"] == "2026-03-27"
    assert selected["selection_reason"] == "match_target_date_with_05_boundary"
    _, selected_none, mode_none = resolve_sleep_for_target_date(target_date="2026-03-27", today_summary=today, history_summaries=[])
    assert selected_none is None
    assert mode_none == "no_valid_candidate"


def test_notes_merge_by_date_and_contract_validation() -> None:
    input_rows = [{"id": "a", "date": "2026-03-27"}, {"id": "b", "date": "2026-03-28"}]
    good_raw = """[{"id":"a","date":"2026-03-27","sentiment":"negative","flags":{"fatigue":false,"stress":true,"social_load":false,"achievement":false,"self_care":false,"sleep_issue":false},"tags":["stress"],"confidence":0.82},{"id":"b","date":"2026-03-28","sentiment":"neutral","flags":{"fatigue":false,"stress":false,"social_load":false,"achievement":false,"self_care":false,"sleep_issue":false},"tags":[],"confidence":0.7}]"""
    _, good_meta = parse_note_label_json_with_meta(good_raw, input_rows)
    assert good_meta["matched_dates_count"] == 2
    assert not good_meta.get("merge_failed")

    bad_raw = """[{"id":"a","date":"2026-03-27","sentiment":"negative","flags":{"fatigue":false,"stress":true,"social_load":false,"achievement":false,"self_care":false,"sleep_issue":false},"tags":["stress"],"confidence":0.82}]"""
    _, bad_meta = parse_note_label_json_with_meta(bad_raw, input_rows)
    assert bad_meta.get("merge_failed")
    assert "2026-03-28" in bad_meta.get("missing_dates", set())


def test_f_risk_state_backend_behavior(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    store = FRiskStateStore()
    assert store.meta.backend == "unavailable"
    assert store.save_for_date("2026-03-28", {"x": 1}) is False

    cfg = daily_job.Config(
        mail_from="", mail_to=[], gmail_app_password="", tasks_closed_url="", daily_log_upsert_url="", daily_log_ensure_url="", health_ingest_url="", expenses_ingest_url="", daily_log_read_url="", diary_generate_url="", diary_mark_notified_url="", bearer_token=None, openai_model=""
    )
    runtime = daily_job._compute_f_risk_alert_runtime(
        cfg,
        summary=SimpleNamespace(
            target_date="2026-03-28",
            resolved_sleep_duration_hours=None,
            sleep_score=None,
            weather_code=None,
            weather_temp_max_c=None,
            weather_temp_min_c=None,
            weather_precip_probability_max=None,
        ),
        run_id="test",
    )
    assert runtime["skip_reason"] == "state_backend_unavailable"

    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    local_store = FRiskStateStore()
    assert local_store.meta.backend in {"local_fallback", "github_branch"}
