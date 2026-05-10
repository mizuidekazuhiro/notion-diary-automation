from __future__ import annotations

import os
import sys
from typing import Any

import requests

NOTION_VERSION = "2022-06-28"


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _strict() -> bool:
    return _enabled("STRICT_NOTION_SCHEMA_AUDIT", default=False)


def _norm_type(t: str) -> str:
    return "rich_text" if t in {"text", "rich_text"} else t


CORE_REQUIRED = {
    "Mail Input Hash": {"rich_text"}, "Mail Input Snapshot": {"rich_text"}, "Mail Sent At": {"date"}, "Mail Version": {"number"},
    "Diary Notification Sent": {"checkbox"}, "Diary Notification Hash": {"rich_text"}, "Diary Notification Sent At": {"date"}, "Diary Notification Version": {"number"},
    "Study Minutes": {"number"}, "Study Sessions": {"number"}, "Study Last Used At": {"date"},
    "Weather": {"select", "rich_text"}, "Weather Summary": {"rich_text"}, "Weather Location": {"rich_text"}, "Weather Temp Max C": {"number"}, "Weather Temp Min C": {"number"},
    "Weather Precip Probability Max": {"number"}, "Weather Code": {"number"}, "Weather Input Hash": {"rich_text"}, "Weather Retrieved At": {"date"}, "Weather Generated At": {"date"},
}
OPTIONAL = {
    "Expense F": ("SAVE_EXPENSE_F_SUMMARY_TO_DAILY_LOG", {
        "Expense F Count": {"number"}, "Expense F Total": {"number"}, "Expense F Merchants": {"rich_text"}, "Expense F Categories": {"rich_text"}, "Expense F First Time": {"date"}, "Expense F Last Time": {"date"}, "Expense F Data Status": {"select", "rich_text"},
    }),
    "F Risk": ("SAVE_F_RISK_TO_DAILY_LOG", {
        "F Risk Alert": {"rich_text"}, "F Risk Score": {"number"}, "F Risk Reason": {"rich_text"}, "F Risk Matched Patterns": {"rich_text"}, "F Risk Input Hash": {"rich_text"}, "F Risk Generated At": {"date"},
    }),
    "Notes Label": ("SAVE_NOTES_LABEL_TO_DAILY_LOG", {
        "Notes Label Input Hash": {"rich_text"}, "Notes Label Generated At": {"date"}, "Notes Label Model": {"rich_text"}, "Notes Sentiment Label": {"select", "rich_text"}, "Notes Sentiment Score": {"number"}, "Notes Stress Flag": {"checkbox"}, "Notes Fatigue Flag": {"checkbox"}, "Notes Social Load Flag": {"checkbox"}, "Notes Sleep Issue Flag": {"checkbox"}, "Notes Flags JSON": {"rich_text"}, "Notes Tags JSON": {"rich_text"},
    }),
}


def _audit_group(name: str, expected: dict[str, set[str]], props: dict[str, Any], active: bool = True) -> list[str]:
    print(f"\n[{name}]")
    if not active:
        print("- SKIPPED (feature disabled)")
        return []
    errors: list[str] = []
    for pname, allowed in expected.items():
        got = props.get(pname)
        if not got:
            errors.append(f"MISSING: {pname}")
            print(f"- MISSING: {pname} expected={sorted(allowed)}")
            continue
        actual = _norm_type(str(got.get("type") or ""))
        allowed_norm = {_norm_type(x) for x in allowed}
        if actual not in allowed_norm:
            errors.append(f"TYPE_MISMATCH: {pname}")
            print(f"- TYPE_MISMATCH: {pname} expected={sorted(allowed_norm)} actual={actual}")
        else:
            print(f"- OK: {pname} ({actual})")
    return errors


def main() -> int:
    token = os.getenv("NOTION_TOKEN", "").strip()
    db_id = os.getenv("DAILY_LOG_DB_ID", "").strip()
    if not token or not db_id:
        print("ERROR: NOTION_TOKEN and DAILY_LOG_DB_ID are required")
        return 1
    resp = requests.get(f"https://api.notion.com/v1/databases/{db_id}", headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}, timeout=20)
    resp.raise_for_status()
    props = (resp.json() or {}).get("properties", {})

    errors = _audit_group("A. Core required / existing", CORE_REQUIRED, props, active=True)
    for group_name, (env_name, spec) in OPTIONAL.items():
        errors += _audit_group(f"B. Optional ({group_name})", spec, props, active=_enabled(env_name, False))

    if errors:
        print("\nSUMMARY: mismatch found")
        if _strict():
            print("STRICT_NOTION_SCHEMA_AUDIT=true -> exit 1")
            return 1
        print("warning only")
    else:
        print("\nSUMMARY: schema audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
