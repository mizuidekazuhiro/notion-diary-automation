import json
import hashlib

import pytest
from cryptography.exceptions import InvalidTag

from scripts.note_label_cache import cache_identifier, decode_cache, encode_cache


def test_cache_roundtrip_contains_neither_notes_nor_key(monkeypatch):
    key = "synthetic-credential-material"
    note = "private-note-evidence-marker"
    monkeypatch.setenv("OPENAI_API_KEY", key)
    payload = {"signals": [{"tag": "achievement", "evidence_text": note}]}
    first = encode_cache(payload, context="one-note-key")
    second = encode_cache(payload, context="one-note-key")
    assert first != second
    assert key.encode() not in first
    assert note.encode() not in first
    assert decode_cache(first, context="one-note-key") == payload
    identity = cache_identifier("2000-01-02\n" + note)
    assert not identity.startswith("2000-")
    assert note not in identity
    assert identity == cache_identifier("2000-01-02\n" + note)
    assert identity != hashlib.sha256(("2000-01-02\n" + note).encode()).hexdigest() + ".json"
    monkeypatch.setenv("OPENAI_API_KEY", "rotated-synthetic-credential")
    assert identity != cache_identifier("2000-01-02\n" + note)


def test_tamper_wrong_identity_and_key_rotation_cannot_adopt_a_label(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "first-synthetic-credential")
    blob = encode_cache({"signals": []}, context="first-note")
    with pytest.raises(InvalidTag):
        decode_cache(blob, context="different-note")
    envelope = json.loads(blob)
    ciphertext = envelope["ciphertext"]
    envelope["ciphertext"] = ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
    with pytest.raises(InvalidTag):
        decode_cache(json.dumps(envelope).encode(), context="first-note")
    monkeypatch.setenv("OPENAI_API_KEY", "rotated-synthetic-credential")
    with pytest.raises(InvalidTag):
        decode_cache(blob, context="first-note")


def test_missing_key_and_old_plaintext_cache_fail_explicitly(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="encryption_key_unavailable"):
        encode_cache({"signals": []}, context="one-note")
    with pytest.raises(ValueError, match="encrypted_note_cache_required"):
        decode_cache(b'{"signals":[]}', context="one-note")


def test_cache_failure_logs_do_not_expose_identity_or_exception_payload(monkeypatch, tmp_path, caplog):
    from pathlib import Path
    from scripts.note_batch_labeler import _cache_key, _load_cached_label, _save_cached_label, neutral_label

    key = "synthetic-log-privacy-credential"
    date = "2088-03-17"
    note = "private-source-marker"
    monkeypatch.delenv("NOTES_LABEL_CACHE_DISABLE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("NOTES_LABEL_CACHE_DIR", str(tmp_path))
    path = tmp_path / _cache_key(date=date, note_text=note, model="test-model")
    path.write_text('{"signals":[]}', encoding="utf-8")
    assert _load_cached_label(date=date, note_text=note, model="test-model") is None
    def denied(*_args, **_kwargs):
        raise OSError(f"sensitive-exception {key} {note} {date}")
    monkeypatch.setattr(Path, "write_bytes", denied)
    assert _save_cached_label(date=date, note_text=note, model="test-model", label=neutral_label(date)) is False
    assert "cache_read_failed" in caplog.text
    assert "cache_write_failed" in caplog.text
    assert "OSError" in caplog.text
    for private_value in (key, date, note, "sensitive-exception", path.name):
        assert private_value not in caplog.text
