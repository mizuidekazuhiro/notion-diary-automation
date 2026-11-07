import sqlite3
import time

import pytest

from scripts import anki_revlog_sync as sync


def test_exclusive_sqlite_lock_has_deadline_and_never_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = tmp_path / "Anki2" / "Profile" / "collection.anki2"
    path.parent.mkdir(parents=True)
    writer = sqlite3.connect(path)
    writer.execute("CREATE TABLE revlog (id INTEGER PRIMARY KEY, time INTEGER)")
    writer.commit()
    writer.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        with pytest.raises(sync.SyncError, match="deadline"):
            sync.collect_reviews_via_sqlite_backup(profile="Profile", start_ms=0, end_ms=2000, timeout_seconds=0.15)
        assert time.monotonic() - started < 2
    finally:
        writer.rollback()
        writer.close()


def test_lifecycle_busy_read_recovers_without_syncing(monkeypatch):
    class Client:
        attempts = 0
        def invoke(self, action, params=None, **kwargs):
            assert action in {"deckNames", "cardReviews"}
            if action == "deckNames":
                self.attempts += 1
                if self.attempts == 1:
                    raise sync.SyncError("Collection lifecycle operation is active")
                return ["deck"]
            return [[1000, 0, 0, 0, 0, 0, 0, 2000]]
    client = Client()
    monkeypatch.setattr(sync.time, "sleep", lambda _: None)
    result = sync.collect_reviews_via_anki_connect(client, start_ms=0)
    assert result == [sync.Review(1000, 2000)]
    assert client.attempts == 2


def test_slow_http_read_respects_remaining_collection_deadline(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(0.25)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"result":[],"error":null}')
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(sync, "ANKI_CONNECT_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        started = time.monotonic()
        with pytest.raises(sync.SyncError):
            sync.collect_reviews_via_anki_connect(sync.AnkiConnectClient(), start_ms=0, timeout_seconds=0.05)
        assert time.monotonic() - started < 0.2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
