from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

NOTION_VERSION = "2022-06-28"
MAJOR_HEALTH_FIELDS: dict[str, tuple[str, ...]] = {
    "sleep_duration_min": ("sleep_duration_min", "Sleep Duration", "Sleep Duration Min"),
    "sleep_score": ("sleep_score", "Sleep Score"),
    "readiness_hrv": ("readiness_hrv", "Readiness HRV"),
    "readiness_bpm": ("readiness_bpm", "Readiness BPM"),
    "kcal": ("kcal", "Kcal"),
    "protein": ("protein", "Protein"),
    "fat": ("fat", "Fat"),
    "carb": ("carb", "Carb"),
}


@dataclass(frozen=True)
class CanaryResult:
    name: str
    status: str
    details: dict[str, Any]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def _request(method: str, url: str, *, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.request(method, url, headers=_headers(token), json=payload, timeout=20)
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise RuntimeError(
            json.dumps(
                {
                    "http_status": response.status_code,
                    "notion_error_code": body.get("code") if isinstance(body, dict) else None,
                    "response_message": " ".join(str((body or {}).get("message") or "request failed").split())[:300] if isinstance(body, dict) else "request failed",
                },
                sort_keys=True,
            )
        )
    body = response.json()
    return body if isinstance(body, dict) else {}


def _number_present(prop: object) -> bool:
    return isinstance(prop, dict) and prop.get("type") == "number" and prop.get("number") is not None


def _normalize_property_name(name: str) -> str:
    return "".join(character.lower() for character in name if character.isalnum())


def _find_property(properties: dict[str, Any], aliases: tuple[str, ...]) -> object:
    by_normalized = {_normalize_property_name(name): value for name, value in properties.items()}
    for alias in aliases:
        prop = by_normalized.get(_normalize_property_name(alias))
        if prop is not None:
            return prop
    return None


def _date_start(prop: object) -> str | None:
    if not isinstance(prop, dict) or prop.get("type") != "date":
        return None
    value = prop.get("date")
    if not isinstance(value, dict):
        return None
    start = value.get("start")
    return str(start).strip() if start else None


def _health_available_fields(properties: dict[str, Any]) -> list[str]:
    return sorted(
        name
        for name, aliases in MAJOR_HEALTH_FIELDS.items()
        if _number_present(_find_property(properties, aliases))
    )


def run_canary(*, token: str, expenses_db_id: str, health_db_id: str, daily_log_db_id: str) -> list[CanaryResult]:
    base = "https://api.notion.com/v1"
    results: list[CanaryResult] = []
    schemas: dict[str, dict[str, Any]] = {}
    for name, db_id in (("expenses", expenses_db_id), ("health", health_db_id), ("daily_log", daily_log_db_id)):
        schema = _request("GET", f"{base}/databases/{db_id}", token=token)
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        schemas[name] = props
        results.append(CanaryResult(f"notion_schema_{name}", "ok", {"property_count": len(props)}))

    expense_filter = {
        "and": [
            {"property": "F", "checkbox": {"equals": True}},
            {"property": "FamilyCard", "checkbox": {"equals": False}},
        ]
    }
    expense_query = _request(
        "POST",
        f"{base}/databases/{expenses_db_id}/query",
        token=token,
        payload={"filter": expense_filter, "page_size": 1},
    )
    known_f_record_found = bool(expense_query.get("results"))
    results.append(
        CanaryResult(
            "expenses_f_query",
            "ok" if known_f_record_found else "no_data",
            {"known_f_record_found": known_f_record_found, "filter_strategy": "f_equals_true_and_family_equals_false"},
        )
    )

    health_query = _request(
        "POST",
        f"{base}/databases/{health_db_id}/query",
        token=token,
        payload={"sorts": [{"property": "Date", "direction": "descending"}], "page_size": 50},
    )
    health_pages = health_query.get("results") if isinstance(health_query.get("results"), list) else []
    health_props = (health_pages[0].get("properties") or {}) if health_pages else {}
    available = _health_available_fields(health_props)
    completeness = round(len(available) / len(MAJOR_HEALTH_FIELDS), 3)
    health_status = "no_data" if not available else "degraded" if completeness < 0.5 else "ok"
    date_property = _find_property(health_props, ("Date", "date"))
    data_date = _date_start(date_property)
    last_valid_at = None
    for page in health_pages:
        if not isinstance(page, dict):
            continue
        props = page.get("properties") if isinstance(page.get("properties"), dict) else {}
        if not _health_available_fields(props):
            continue
        last_valid_at = str(page.get("last_edited_time") or "").strip() or _date_start(_find_property(props, ("Date", "date")))
        break
    results.append(
        CanaryResult(
            "health_latest_quality",
            health_status,
            {
                "page_found": bool(health_pages),
                "data_date": data_date,
                "last_valid_at": last_valid_at,
                "available_fields": available,
                "completeness": completeness,
                "error_code": "major_fields_empty" if health_status == "no_data" else "low_completeness" if health_status == "degraded" else None,
            },
        )
    )

    daily_query = _request(
        "POST",
        f"{base}/databases/{daily_log_db_id}/query",
        token=token,
        payload={"sorts": [{"property": "Date", "direction": "descending"}], "page_size": 1},
    )
    results.append(CanaryResult("daily_log_latest_read", "ok" if daily_query.get("results") else "no_data", {"page_found": bool(daily_query.get("results"))}))
    return results


def main() -> None:
    required = {name: os.getenv(name, "").strip() for name in ("NOTION_TOKEN", "EXPENSES_DB_ID", "HEALTH_DB_ID", "DAILY_LOG_DB_ID")}
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"missing required environment variables: {','.join(missing)}")
    results = run_canary(
        token=required["NOTION_TOKEN"],
        expenses_db_id=required["EXPENSES_DB_ID"],
        health_db_id=required["HEALTH_DB_ID"],
        daily_log_db_id=required["DAILY_LOG_DB_ID"],
    )
    for result in results:
        print(json.dumps({"check": result.name, "status": result.status, **result.details}, sort_keys=True))
    if any(result.status != "ok" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
