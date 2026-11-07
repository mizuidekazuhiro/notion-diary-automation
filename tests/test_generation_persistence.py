from types import SimpleNamespace
import pytest

from scripts import daily_job


def test_readback_rejects_dropped_evidence_even_when_body_saved(monkeypatch):
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda *args: SimpleNamespace(diary="body", diary_evidence_json=None))
    with pytest.raises(RuntimeError, match="diary_evidence_json"):
        daily_job._verify_generation_readback(None, "2026-11-06", {"diary": "body", "diary_evidence_json": '{"claims":[]}'} )


def test_readback_compares_metadata_semantically_and_rejects_wrong_body(monkeypatch):
    row = SimpleNamespace(diary="body", generation_metadata='{"advice": {"status": "success"}}')
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda *args: row)
    assert daily_job._verify_generation_readback(None, "2026-11-06", {"diary": "body", "generation_metadata": '{"advice":{"status":"success"}}'}) is row
    with pytest.raises(RuntimeError, match="diary"):
        daily_job._verify_generation_readback(None, "2026-11-06", {"diary": "body truncated"})
