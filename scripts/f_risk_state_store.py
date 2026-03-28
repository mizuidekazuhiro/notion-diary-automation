from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests


STATE_BRANCH = "automation-state"
STATE_PATH = ".state/f_risk_state.json"
LOCAL_FALLBACK_PATH = Path(".runtime/f_risk_state.local.json")
GITHUB_API_BASE = "https://api.github.com"


@dataclass(frozen=True)
class StateStoreMeta:
    backend: str
    branch_name: str
    path: str
    fallback_used: bool
    state_read_ok: bool
    state_write_ok: bool


class FRiskStateStore:
    def __init__(self) -> None:
        self._token = (os.getenv("GITHUB_TOKEN") or "").strip()
        self._repo = (os.getenv("GITHUB_REPOSITORY") or "").strip()
        self._is_ci = (os.getenv("GITHUB_ACTIONS") or "").strip().lower() == "true"
        self._github_enabled = bool(self._token and self._repo)
        self._cached_sha: Optional[str] = None
        backend = "github_branch" if self._github_enabled else ("unavailable" if self._is_ci else "local_fallback")
        self._meta = StateStoreMeta(
            backend=backend,
            branch_name=STATE_BRANCH,
            path=STATE_PATH if self._github_enabled else ("" if self._is_ci else str(LOCAL_FALLBACK_PATH)),
            fallback_used=(not self._github_enabled) and (not self._is_ci),
            state_read_ok=self._github_enabled,
            state_write_ok=False,
        )

    @property
    def meta(self) -> StateStoreMeta:
        return self._meta

    def load_all(self) -> dict[str, Any]:
        if self._github_enabled:
            return self._load_all_from_github()
        if self._is_ci:
            self._meta = StateStoreMeta("unavailable", STATE_BRANCH, "", False, False, False)
            return self._empty_state()
        return self._load_all_from_local()

    def get_for_date(self, target_date: str) -> dict[str, Any]:
        payload = self.load_all()
        by_date = payload.get("by_date", {})
        if not isinstance(by_date, dict):
            return {}
        row = by_date.get(target_date)
        return row if isinstance(row, dict) else {}

    def save_for_date(self, target_date: str, row: dict[str, Any]) -> bool:
        payload = self.load_all()
        by_date = payload.setdefault("by_date", {})
        if not isinstance(by_date, dict):
            by_date = {}
            payload["by_date"] = by_date
        by_date[target_date] = row
        payload["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if self._github_enabled:
            ok = self._save_all_to_github(payload)
        elif self._is_ci:
            ok = False
        else:
            ok = self._save_all_to_local(payload)
        self._meta = StateStoreMeta(
            backend=self._meta.backend,
            branch_name=self._meta.branch_name,
            path=self._meta.path,
            fallback_used=self._meta.fallback_used,
            state_read_ok=self._meta.state_read_ok,
            state_write_ok=ok,
        )
        return ok

    def _empty_state(self) -> dict[str, Any]:
        return {"version": 1, "by_date": {}, "updated_at": None}

    def _github_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _repo_api(self, suffix: str) -> str:
        return f"{GITHUB_API_BASE}/repos/{self._repo}{suffix}"

    def _ensure_state_branch(self) -> bool:
        ref_url = self._repo_api(f"/git/ref/heads/{STATE_BRANCH}")
        resp = requests.get(ref_url, headers=self._github_headers(), timeout=20)
        if resp.status_code == 200:
            return True
        if resp.status_code != 404:
            return False
        repo_resp = requests.get(self._repo_api(""), headers=self._github_headers(), timeout=20)
        if repo_resp.status_code >= 400:
            return False
        default_branch = (repo_resp.json() or {}).get("default_branch") or "main"
        default_ref_resp = requests.get(
            self._repo_api(f"/git/ref/heads/{default_branch}"),
            headers=self._github_headers(),
            timeout=20,
        )
        if default_ref_resp.status_code >= 400:
            return False
        sha = ((default_ref_resp.json() or {}).get("object") or {}).get("sha")
        if not sha:
            return False
        create_resp = requests.post(
            self._repo_api("/git/refs"),
            headers=self._github_headers(),
            json={"ref": f"refs/heads/{STATE_BRANCH}", "sha": sha},
            timeout=20,
        )
        return create_resp.status_code < 400

    def _load_all_from_github(self) -> dict[str, Any]:
        if not self._ensure_state_branch():
            if self._is_ci:
                self._meta = StateStoreMeta("unavailable", STATE_BRANCH, "", False, False, False)
                return self._empty_state()
            return self._load_all_from_local()
        url = self._repo_api(f"/contents/{STATE_PATH}")
        resp = requests.get(
            url,
            headers=self._github_headers(),
            params={"ref": STATE_BRANCH},
            timeout=20,
        )
        if resp.status_code == 404:
            self._cached_sha = None
            payload = self._empty_state()
            self._meta = StateStoreMeta("github_branch", STATE_BRANCH, STATE_PATH, False, True, self._meta.state_write_ok)
            return payload
        if resp.status_code >= 400:
            if self._is_ci:
                self._meta = StateStoreMeta("unavailable", STATE_BRANCH, "", False, False, False)
                return self._empty_state()
            return self._load_all_from_local()
        body = resp.json() or {}
        self._cached_sha = body.get("sha")
        content = body.get("content") or ""
        encoding = body.get("encoding")
        if encoding != "base64":
            return self._empty_state()
        try:
            decoded = base64.b64decode(content).decode("utf-8")
            payload = json.loads(decoded)
            if not isinstance(payload, dict):
                payload = self._empty_state()
        except Exception:
            payload = self._empty_state()
        self._meta = StateStoreMeta("github_branch", STATE_BRANCH, STATE_PATH, False, True, self._meta.state_write_ok)
        return payload

    def _save_all_to_github(self, payload: dict[str, Any]) -> bool:
        if not self._ensure_state_branch():
            return False
        body = {
            "message": f"chore(state): update F risk state {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "content": base64.b64encode(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            ).decode("ascii"),
            "branch": STATE_BRANCH,
        }
        if self._cached_sha:
            body["sha"] = self._cached_sha
        url = self._repo_api(f"/contents/{STATE_PATH}")
        resp = requests.put(url, headers=self._github_headers(), json=body, timeout=20)
        if resp.status_code >= 400:
            return False
        content = (resp.json() or {}).get("content") or {}
        self._cached_sha = content.get("sha")
        return True

    def _load_all_from_local(self) -> dict[str, Any]:
        try:
            raw = LOCAL_FALLBACK_PATH.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("state payload is not dict")
        except Exception:
            payload = self._empty_state()
        self._meta = StateStoreMeta("local_fallback", STATE_BRANCH, str(LOCAL_FALLBACK_PATH), True, True, self._meta.state_write_ok)
        return payload

    def _save_all_to_local(self, payload: dict[str, Any]) -> bool:
        try:
            LOCAL_FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOCAL_FALLBACK_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            return True
        except Exception:
            logging.exception("f_risk_state_local_write_failed")
            return False
