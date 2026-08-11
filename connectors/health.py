from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ingest.http_client import post_json


@dataclass(frozen=True)
class HealthIngestResult:
    target_date: str
    ok: bool
    payload: Dict[str, Any]
    status: str
    data_date: Optional[str]
    last_valid_at: Optional[str]
    completeness: float
    available_fields: tuple[str, ...]
    error_code: Optional[str] = None
    error: Optional[str] = None


class HealthConnector:
    id = "health"

    def __init__(self, ingest_url: str, bearer_token: Optional[str]) -> None:
        self.ingest_url = ingest_url
        self.bearer_token = bearer_token

    def fetch(self, target_date: str) -> HealthIngestResult:
        payload = {"target_date": target_date}
        try:
            response = post_json(self.ingest_url, payload, self.bearer_token)
            quality = _evaluate_health_response(target_date, response)
            return HealthIngestResult(
                target_date=target_date,
                ok=quality["status"] == "ok",
                payload=response,
                status=quality["status"],
                data_date=quality["data_date"],
                last_valid_at=quality["last_valid_at"],
                completeness=quality["completeness"],
                available_fields=tuple(quality["available_fields"]),
                error_code=quality["error_code"],
            )
        except Exception as exc:  # noqa: BLE001 - ensure Phase A continues
            logging.warning("health_ingest_failed exception_class=%s error_code=connector_exception", exc.__class__.__name__)
            return HealthIngestResult(
                target_date=target_date,
                ok=False,
                payload={},
                status="failed",
                data_date=None,
                last_valid_at=None,
                completeness=0.0,
                available_fields=(),
                error_code="connector_exception",
                error=exc.__class__.__name__,
            )

    def render(self, result: HealthIngestResult) -> Dict[str, Any]:
        raw_payload = {
            "target_date": result.target_date,
            "ok": result.ok,
            "status": result.status,
            "data_date": result.data_date,
            "last_valid_at": result.last_valid_at,
            "completeness": result.completeness,
            "available_fields": list(result.available_fields),
            "error_code": result.error_code,
            "payload": result.payload,
            "error": result.error,
        }
        return {
            "summary_blocks": {},
            "raw_payload": raw_payload,
        }


HEALTH_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "sleep_duration_min": ("sleep_duration_min", "sleepDurationMin", "Sleep Duration Min"),
    "sleep_score": ("sleep_score", "sleepScore", "Sleep Score"),
    "readiness_hrv": ("readiness_hrv", "readinessHrv", "Readiness HRV"),
    "readiness_bpm": ("readiness_bpm", "readinessBpm", "Readiness BPM"),
    "kcal": ("kcal", "Kcal"),
    "protein": ("protein", "Protein"),
    "fat": ("fat", "Fat"),
    "carb": ("carb", "Carb"),
}


def _find_value(payload: object, aliases: tuple[str, ...]) -> object:
    if isinstance(payload, dict):
        for alias in aliases:
            if alias in payload and payload[alias] not in (None, "", [], {}):
                return payload[alias]
        for value in payload.values():
            found = _find_value(value, aliases)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_value(value, aliases)
            if found not in (None, "", [], {}):
                return found
    return None


def _evaluate_health_response(target_date: str, response: Dict[str, Any]) -> Dict[str, Any]:
    supplied = response.get("health_quality") if isinstance(response.get("health_quality"), dict) else {}
    available = supplied.get("available_fields")
    if not isinstance(available, list):
        available = [name for name, aliases in HEALTH_FIELD_ALIASES.items() if _find_value(response, aliases) is not None]
    available = sorted({str(name) for name in available})
    completeness = supplied.get("completeness")
    if not isinstance(completeness, (int, float)):
        completeness = round(len(available) / len(HEALTH_FIELD_ALIASES), 3)
    data_date = str(supplied.get("data_date") or response.get("data_date") or response.get("target_date") or "").strip() or None
    last_valid_at = str(supplied.get("last_valid_at") or response.get("last_valid_at") or "").strip() or None
    error_code = str(supplied.get("error_code") or response.get("error_code") or "").strip() or None
    if not bool(response.get("ok", False)):
        status = "failed"
        error_code = error_code or "upstream_failed"
    elif data_date and data_date != target_date:
        status = "stale"
        error_code = error_code or "data_date_mismatch"
    elif not available:
        status = "no_data"
        error_code = error_code or "major_fields_empty"
    elif float(completeness) < 0.5:
        status = "degraded"
        error_code = error_code or "low_completeness"
    else:
        status = "ok"
    logging.info(
        "health_freshness status=%s data_date=%s last_valid_at=%s completeness=%.3f available_fields=%s error_code=%s",
        status,
        data_date,
        last_valid_at,
        float(completeness),
        available,
        error_code,
    )
    return {
        "status": status,
        "data_date": data_date,
        "last_valid_at": last_valid_at,
        "completeness": round(float(completeness), 3),
        "available_fields": available,
        "error_code": error_code,
    }
