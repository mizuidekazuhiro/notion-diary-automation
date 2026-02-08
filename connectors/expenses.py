from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ingest.http_client import post_json


@dataclass(frozen=True)
class ExpensesIngestResult:
    target_date: str
    ok: bool
    payload: Dict[str, Any]
    error: Optional[str] = None


class ExpensesConnector:
    id = "expenses"

    def __init__(self, ingest_url: str, bearer_token: Optional[str]) -> None:
        self.ingest_url = ingest_url
        self.bearer_token = bearer_token

    def fetch(self, target_date: str) -> ExpensesIngestResult:
        payload = {"target_date": target_date}
        try:
            response = post_json(self.ingest_url, payload, self.bearer_token)
            ok = bool(response.get("ok", False))
            return ExpensesIngestResult(
                target_date=target_date,
                ok=ok,
                payload=response,
            )
        except Exception as exc:  # noqa: BLE001 - ensure Phase A continues
            logging.warning("Expenses ingest connector failed: %s", exc)
            return ExpensesIngestResult(
                target_date=target_date,
                ok=False,
                payload={},
                error=str(exc),
            )

    def render(self, result: ExpensesIngestResult) -> Dict[str, Any]:
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
