from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ingest.http_client import post_json


@dataclass(frozen=True)
class WorkoutIngestResult:
    target_date: str
    ok: bool
    payload: Dict[str, Any]
    error: Optional[str] = None


class WorkoutConnector:
    id = "workout"

    def __init__(self, ingest_url: str, bearer_token: Optional[str]) -> None:
        self.ingest_url = ingest_url
        self.bearer_token = bearer_token

    def fetch(self, target_date: str) -> WorkoutIngestResult:
        try:
            response = post_json(
                self.ingest_url,
                {"target_date": target_date},
                self.bearer_token,
            )
            return WorkoutIngestResult(
                target_date=target_date,
                ok=bool(response.get("ok", False)),
                payload=response,
            )
        except Exception as exc:  # noqa: BLE001 - do not block other daily sources
            logging.warning("Workout ingest connector failed: %s", exc)
            return WorkoutIngestResult(
                target_date=target_date,
                ok=False,
                payload={},
                error=str(exc),
            )

    def render(self, result: WorkoutIngestResult) -> Dict[str, Any]:
        return {
            "summary_blocks": {},
            "raw_payload": {
                "target_date": result.target_date,
                "ok": result.ok,
                "payload": result.payload,
                "error": result.error,
            },
        }
