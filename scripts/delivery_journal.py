"""Durable SMTP claim. An ambiguous acceptance must never be auto-retried."""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests


class DeliveryJournal:
    def __init__(self, *, directory: Path | None = None) -> None:
        self.directory = directory or Path(os.getenv("MAIL_JOURNAL_DIR", ".runtime/mail_journal"))
        self.token = os.getenv("GITHUB_TOKEN", "").strip()
        self.repo = os.getenv("GITHUB_REPOSITORY", "").strip()
        self.remote = directory is None and os.getenv("MAIL_JOURNAL_BACKEND", "") != "local" and bool(self.repo and self.token)
        if directory is None and os.getenv("GITHUB_ACTIONS") == "true" and not self.remote and os.getenv("MAIL_JOURNAL_BACKEND") != "local":
            raise RuntimeError("durable_delivery_journal_unavailable")
        self.previous_state: dict = {}
        self.payload: dict = {}
        self.sha: str | None = None
        self.key = ""

    def _url(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/contents/.state/delivery/{self.key}.json"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    def _remote_read(self) -> dict | None:
        response = requests.get(self._url(), headers=self._headers(), params={"ref": "automation-state"}, timeout=20)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        self.sha = data["sha"]
        return json.loads(base64.b64decode(data["content"]))

    def _remote_write(self, *, creating: bool = False) -> bool:
        body = {"message": "chore(state): update delivery receipt", "branch": "automation-state",
                "content": base64.b64encode(json.dumps(self.payload, sort_keys=True).encode()).decode()}
        if self.sha:
            body["sha"] = self.sha
        response = requests.put(self._url(), headers=self._headers(), json=body, timeout=20)
        if creating and response.status_code in {409, 422}:
            previous = self._remote_read()
            if previous is None:
                raise RuntimeError("delivery_claim_not_persisted")
            self.previous_state = previous
            return False
        response.raise_for_status()
        self.sha = response.json()["content"]["sha"]
        return True

    def claim(self, key: str, render_hash: str) -> bool:
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("invalid_delivery_key")
        self.key = key
        self.payload = {"version": 1, "state": "sending", "message_hash": key, "render_hash": render_hash,
                        "updated_at": datetime.now(timezone.utc).isoformat()}
        if self.remote:
            previous = self._remote_read()
            if previous is not None:
                self.previous_state = previous
                return False
            return self._remote_write(creating=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.json"
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(self.payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            return True
        except FileExistsError:
            try:
                self.previous_state = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.previous_state = {"state": "uncertain"}
            return False

    def finish(self, state: str) -> None:
        if state not in {"sent", "uncertain"} or not self.key:
            raise ValueError("invalid_delivery_state")
        self.payload.update(state=state, updated_at=datetime.now(timezone.utc).isoformat())
        if self.remote:
            self._remote_write()
        else:
            path = self.directory / f"{self.key}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.payload), encoding="utf-8")
            temporary.replace(path)
