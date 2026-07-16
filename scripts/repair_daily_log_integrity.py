from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

TITLE_SEPARATORS = ("｜", "|", "❘")
REQUIRED_PHASE_ABC = ("Target Date", "Activity Summary", "Mail ID", "Today advice", "Diary", "Today Advice Generated At", "Diary Generated At")


def canonical_title(date: str) -> str:
    return f"Daily Log｜{date}"


def extract_title_date(title: str) -> str | None:
    import re
    m = re.match(r"^Daily\s*Log\s*(?:｜|\||❘)?\s*(\d{4}-\d{2}-\d{2})$", (title or "").strip(), re.I)
    return m.group(1) if m else None


def prop_date(props: Mapping[str, Any], name: str) -> str | None:
    v = props.get(name, {})
    s = ((v.get("date") or {}).get("start") if isinstance(v, Mapping) else None)
    return s[:10] if isinstance(s, str) and s else None


def prop_empty(v: Any) -> bool:
    if not v: return True
    if isinstance(v, Mapping) and "rich_text" in v: return not any((x.get("plain_text") or "").strip() for x in v.get("rich_text") or [])
    if isinstance(v, Mapping) and "title" in v: return not any((x.get("plain_text") or "").strip() for x in v.get("title") or [])
    if isinstance(v, Mapping) and "date" in v: return not ((v.get("date") or {}).get("start"))
    return False


@dataclass
class AuditFinding:
    page_id: str
    code: str
    message: str
    safe_fix: dict[str, Any] = field(default_factory=dict)


def audit_page(page: Mapping[str, Any]) -> list[AuditFinding]:
    props = page.get("properties") or {}
    page_id = str(page.get("id") or "")
    title_prop = props.get("名前") or props.get("Name") or props.get("title") or {}
    title = "".join(x.get("plain_text") or "" for x in title_prop.get("title") or []) if isinstance(title_prop, Mapping) else ""
    date = prop_date(props, "Date")
    target = prop_date(props, "Target Date")
    title_date = extract_title_date(title)
    findings: list[AuditFinding] = []
    official = date or target or title_date
    if date and target and date != target:
        findings.append(AuditFinding(page_id, "date_target_mismatch", f"Date={date} Target Date={target}"))
    if official and title != canonical_title(official):
        findings.append(AuditFinding(page_id, "noncanonical_title", f"title={title!r} official_date={official}", {"title": canonical_title(official)}))
    if date and not target:
        findings.append(AuditFinding(page_id, "target_date_missing", "Target Date is empty", {"Target Date": date}))
    if target and not date:
        findings.append(AuditFinding(page_id, "date_missing", "Date is empty", {"Date": target}))
    if official and title_date and title_date != official:
        findings.append(AuditFinding(page_id, "title_date_mismatch", f"title_date={title_date} official_date={official}", {"title": canonical_title(official)}))
    missing = [k for k in REQUIRED_PHASE_ABC if prop_empty(props.get(k))]
    if missing:
        findings.append(AuditFinding(page_id, "phase_abc_incomplete", ",".join(missing)))
    if not date or not target:
        findings.append(AuditFinding(page_id, "date_or_target_date_empty", f"Date={date} Target Date={target}"))
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit and safely repair Daily Log integrity. Defaults to dry-run and never touches production unless explicit flags are supplied.")
    ap.add_argument("--input-json", help="Offline Notion pages JSON for CI/local auditing")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--apply-safe-fixes", action="store_true")
    ap.add_argument("--merge-duplicates", action="store_true")
    ap.add_argument("--archive-duplicates", action="store_true")
    args = ap.parse_args()
    if (args.merge_duplicates or args.archive_duplicates) and not args.apply_safe_fixes:
        raise SystemExit("Destructive duplicate handling requires --apply-safe-fixes plus explicit duplicate flags")
    pages = []
    if args.input_json:
        with open(args.input_json, encoding="utf-8") as f: pages = json.load(f)
    findings = [f for page in pages for f in audit_page(page)]
    print(json.dumps({"dry_run": not args.apply_safe_fixes, "findings": [asdict(f) for f in findings]}, ensure_ascii=False, indent=2))
    if args.apply_safe_fixes:
        print("apply_safe_fixes_requested=true (wire this to Notion client only during manual operations)")


if __name__ == "__main__":
    main()
