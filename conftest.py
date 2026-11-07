"""Isolate durable external state for both scripts/ and tests/ unit suites."""
import pytest


@pytest.fixture(autouse=True)
def isolate_delivery_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIL_JOURNAL_BACKEND", "local")
    monkeypatch.setenv("MAIL_JOURNAL_DIR", str(tmp_path / "mail-journal"))
    monkeypatch.setenv("DAILY_MAIL_ARTIFACT_DIR", str(tmp_path / "quality-artifacts"))
