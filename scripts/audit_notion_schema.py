from __future__ import annotations

import os
import sys
from typing import Any

import requests

NOTION_VERSION = "2022-06-28"


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _norm_type(value: str) -> str:
    if value in {"text", "rich_text"}:
        return "rich_text"
    return value


def _check(props: dict[str, Any], required: dict[str, set[str]]) -> list[str]:
    errs: list[str] = []
    for name, allowed in required.items():
        got = props.get(name, {}).get("type") if isinstance(props.get(name), dict) else None
        if got is None:
            errs.append(f"missing:{name}")
            continue
        if _norm_type(str(got)) not in {_norm_type(x) for x in allowed}:
            errs.append(f"type_mismatch:{name}:got={got}:want={sorted(allowed)}")
    return errs


def main() -> int:
    token = os.getenv("NOTION_TOKEN", "").strip()
    db_id = os.getenv("DAILY_LOG_DB_ID", "").strip()
    strict = _enabled("STRICT_NOTION_SCHEMA_AUDIT")
    if not token or not db_id:
        print("schema_audit skipped: missing NOTION_TOKEN/DAILY_LOG_DB_ID")
        return 0
    resp = requests.get(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
        timeout=20,
    )
    resp.raise_for_status()
    props = (resp.json() or {}).get("properties") or {}

    core = {
        "Mail Input Hash": {"rich_text", "text"}, "Mail Input Snapshot": {"rich_text", "text"}, "Mail Sent At": {"date"}, "Mail Version": {"number"},
        "Diary Notification Sent": {"checkbox"}, "Diary Notification Hash": {"rich_text", "text"}, "Diary Notification Sent At": {"date"}, "Diary Notification Version": {"number"},
    }
    expense = {"Expense F Count": {"number"}, "Expense F Data Status": {"select"}}
    frisk = {"F Risk Alert": {"rich_text", "text"}, "F Risk Score": {"number"}, "F Risk Generated At": {"date"}}
    notes = {"Notes Label Input Hash": {"rich_text", "text"}, "Notes Sentiment Label": {"select"}, "Notes Label Generated At": {"date"}}

    failures = _check(props, core)
    categories = [
        ("expense", _enabled("SAVE_EXPENSE_F_SUMMARY_TO_DAILY_LOG"), expense),
        ("f_risk", _enabled("SAVE_F_RISK_TO_DAILY_LOG"), frisk),
        ("notes", _enabled("SAVE_NOTES_LABEL_TO_DAILY_LOG"), notes),
    ]
    for name, enabled, spec in categories:
        if not enabled:
            print(f"{name}: SKIPPED")
            continue
        failures.extend(_check(props, spec))

    if failures:
        print("schema_audit_failures", failures)
        return 1 if strict else 0
    print("schema_audit_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
