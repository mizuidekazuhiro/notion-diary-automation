from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ingest.http_client import post_json


@dataclass(frozen=True)
class LocationIngestResult:
    target_date: str
    ok: bool
    payload: Dict[str, Any]
    error: Optional[str] = None


class LocationConnector:
    id = "location"

    def __init__(self, ingest_url: str, bearer_token: Optional[str]) -> None:
        self.ingest_url = ingest_url
        self.bearer_token = bearer_token

    def fetch(self, target_date: str) -> LocationIngestResult:
        payload = {"target_date": target_date}
        try:
            response = post_json(self.ingest_url, payload, self.bearer_token)
            ok = bool(response.get("ok", False))
            return LocationIngestResult(
                target_date=target_date,
                ok=ok,
                payload=response,
            )
        except Exception as exc:  # noqa: BLE001 - keep ingest pipeline running
            logging.warning("Location ingest connector failed: %s", exc)
            return LocationIngestResult(
                target_date=target_date,
                ok=False,
                payload={},
                error=str(exc),
            )

    def render(self, result: LocationIngestResult) -> Dict[str, Any]:
        raw_payload = {
            "target_date": result.target_date,
            "ok": result.ok,
            "payload": result.payload,
            "error": result.error,
        }
        return {
            "summary_blocks": {},
            "raw_payload": raw_payload,
        }
