from types import SimpleNamespace

from scripts.daily_job import _compute_expense_f_alert
from scripts.expense_f_aggregator import ExpenseFAggregate
from scripts.location_for_weather import resolve_location_for_weather
from scripts.note_batch_labeler import label_notes_in_batches, parse_note_label_json_with_meta


def test_expense_f_section_hidden_when_no_flag(monkeypatch):
    def fake_agg(_date: str):
        return ExpenseFAggregate(True, 0, 0.0, [], None, None, "no_results", {}, None)

    monkeypatch.setattr("scripts.daily_job.aggregate_daily_expense_f", fake_agg)
    alert = _compute_expense_f_alert(summary=SimpleNamespace(target_date="2026-03-28"), run_id="r1")
    assert alert["matched"] is False
    assert alert["summary"] == ""
    assert alert["reasons"] == []


def test_expense_f_section_when_flagged(monkeypatch):
    def fake_agg(_date: str):
        return ExpenseFAggregate(
            True,
            2,
            3200.0,
            ["A", "B"],
            "2026-03-28T10:00:00Z",
            "2026-03-28T20:00:00Z",
            "ok",
            {},
            None,
        )

    monkeypatch.setattr("scripts.daily_job.aggregate_daily_expense_f", fake_agg)
    alert = _compute_expense_f_alert(summary=SimpleNamespace(target_date="2026-03-28"), run_id="r2")
    assert alert["matched"] is True
    assert "Fプロパティ" in alert["title"]
    assert any("件数" in r for r in alert["reasons"])
    assert any("再発防止" in r for r in alert["reasons"])


def test_notes_date_merge_meta_reports_failures():
    input_rows = [
        {"id": "n1", "date": "2026-03-27", "notes": "a"},
        {"id": "n2", "date": "2026-03-28", "notes": "b"},
    ]
    raw = '[{"id":"x1","date":"2026-03-27","signals":[]},{"id":"x2","signals":[]}]'
    _parsed, meta = parse_note_label_json_with_meta(raw, input_rows)
    assert meta["merge_failed"] is True
    assert meta["unknown_ids"]
    assert meta["merge_failed_reason"] is not None


def test_notes_partial_retry_keeps_successful_results():
    rows = [SimpleNamespace(target_date="2026-03-27", notes="疲れた"), SimpleNamespace(target_date="2026-03-28", notes="進んだ")]

    calls = {"n": 0}

    def fake_chat_completion(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"rows":[{"id":"bad","date":"2026-03-27","signals":[]}]}'
        if calls["n"] == 2:
            return '{"rows":[{"id":"note_0000_2026-03-27","date":"2026-03-27","signals":[{"tag":"fatigue","confidence":0.9}]}]}'
        return '{"rows":[{"id":"note_0001_2026-03-28","date":"2026-03-28","signals":[{"tag":"achievement","confidence":0.9}]}]}'

    audit = {}
    labels = label_notes_in_batches(rows, chat_completion=fake_chat_completion, model="x", batch_size=8, audit=audit)
    assert labels["2026-03-27"].fatigue_flag is True
    assert labels["2026-03-28"].achievement_flag is True
    assert "notes_date_merge_success_rate" in audit


def test_location_weather_prefers_latest_log_latlon(monkeypatch):
    def fake_query(_now):
        return (
            {
                "name": "Shibuya",
                "selected_place": "Shibuya",
                "latitude": 35.66,
                "longitude": 139.70,
                "resolution_method": "location_log_latest_latlon",
            },
            {"query_status": "ok"},
        )

    monkeypatch.setattr("scripts.location_for_weather._query_location_log_place", fake_query)
    resolved = resolve_location_for_weather(summary=SimpleNamespace(place="Ignored", location_summary="Ignored summary"))
    assert resolved.source == "location_log_db_latest"
    assert resolved.latitude == 35.66
    assert resolved.resolution_method == "location_log_latest_latlon"


def test_location_weather_geocode_fallback(monkeypatch):
    def fake_query(_now):
        return (
            {"name": "Shibuya", "selected_place": "Shibuya", "resolution_method": "location_log_latest_place"},
            {"query_status": "ok", "geocode_attempted": False},
        )

    monkeypatch.setattr("scripts.location_for_weather._query_location_log_place", fake_query)
    monkeypatch.setattr("scripts.location_for_weather._load_geocode_cache", lambda: {})
    monkeypatch.setattr("scripts.location_for_weather._save_geocode_cache", lambda _cache: None)
    monkeypatch.setattr("scripts.location_for_weather._geocode_place", lambda _place: (35.66, 139.70, {"status": "ok"}))
    resolved = resolve_location_for_weather(summary=SimpleNamespace(place="fallback"))
    assert resolved.resolution_method == "location_log_latest_place_geocode"
    assert resolved.latitude == 35.66
