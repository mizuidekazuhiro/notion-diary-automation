from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

REQUIRED_PHASE_ABC = ("Target Date", "Activity Summary", "Mail ID", "Today advice", "Diary", "Today Advice Generated At", "Diary Generated At")
TITLE_RE = re.compile(r"^Daily\s*Log\s*(?:｜|\||❘)?\s*(\d{4}-\d{2}-\d{2})$", re.I)


def canonical_title(date: str) -> str:
    return f"Daily Log｜{date}"


def extract_title_date(title: str) -> str | None:
    m = TITLE_RE.match((title or "").strip())
    return m.group(1) if m else None


def prop_date(props: Mapping[str, Any], name: str) -> str | None:
    v = props.get(name, {})
    s = ((v.get("date") or {}).get("start") if isinstance(v, Mapping) else None)
    return s[:10] if isinstance(s, str) and s else None


def prop_text(props: Mapping[str, Any], name: str) -> str:
    v = props.get(name, {})
    if not isinstance(v, Mapping):
        return ""
    for key in ("rich_text", "title"):
        items = v.get(key)
        if isinstance(items, list):
            return "".join(str(x.get("plain_text") or x.get("text", {}).get("content") or "") for x in items if isinstance(x, Mapping)).strip()
    return ""


def prop_empty(props: Mapping[str, Any], name: str) -> bool:
    v = props.get(name)
    if not v:
        return True
    if isinstance(v, Mapping) and ("rich_text" in v or "title" in v):
        return not prop_text(props, name)
    if isinstance(v, Mapping) and "date" in v:
        return not ((v.get("date") or {}).get("start"))
    return False


def title_from_page(page: Mapping[str, Any]) -> str:
    props = page.get("properties") or {}
    if not isinstance(props, Mapping):
        return ""
    for name in ("名前", "Name", "title"):
        if name in props:
            return prop_text(props, name)
    return ""


@dataclass
class AuditFinding:
    page_id: str
    code: str
    message: str
    official_date: str | None = None
    safe_fix: dict[str, Any] = field(default_factory=dict)


def official_date_for_page(page: Mapping[str, Any]) -> tuple[str | None, bool, str | None, str | None, str | None]:
    props = page.get("properties") or {}
    date = prop_date(props, "Date") if isinstance(props, Mapping) else None
    target = prop_date(props, "Target Date") if isinstance(props, Mapping) else None
    title_date = extract_title_date(title_from_page(page))
    if date and target and date != target:
        return None, True, date, target, title_date
    return date or target or title_date, False, date, target, title_date


def audit_page(page: Mapping[str, Any]) -> list[AuditFinding]:
    props = page.get("properties") or {}
    if not isinstance(props, Mapping):
        props = {}
    page_id = str(page.get("id") or "")
    title = title_from_page(page)
    official, ambiguous, date, target, title_date = official_date_for_page(page)
    findings: list[AuditFinding] = []
    if ambiguous:
        findings.append(AuditFinding(page_id, "daily_log_date_ambiguity", f"Date={date} Target Date={target} title_date={title_date}"))
    if date and target and date != target:
        findings.append(AuditFinding(page_id, "date_target_mismatch", f"Date={date} Target Date={target}"))
    if not date:
        findings.append(AuditFinding(page_id, "date_missing", "Date is empty", official))
    if not target:
        findings.append(AuditFinding(page_id, "target_date_missing", "Target Date is empty", official))
    if official and title != canonical_title(official):
        findings.append(AuditFinding(page_id, "noncanonical_title", f"title={title!r} official_date={official}", official, {"title": canonical_title(official)}))
    if official and title_date and title_date != official:
        findings.append(AuditFinding(page_id, "title_date_mismatch", f"title_date={title_date} official_date={official}", official, {"title": canonical_title(official)}))
    missing = [k for k in REQUIRED_PHASE_ABC if prop_empty(props, k)]
    if missing:
        findings.append(AuditFinding(page_id, "phase_abc_incomplete", ",".join(missing), official))
    return findings


def audit_pages(pages: list[Mapping[str, Any]]) -> dict[str, Any]:
    findings = [f for page in pages for f in audit_page(page)]
    groups: dict[str, list[str]] = {}
    for page in pages:
        official, ambiguous, *_ = official_date_for_page(page)
        if official and not ambiguous:
            groups.setdefault(official, []).append(str(page.get("id") or ""))
    for date, ids in groups.items():
        if len(ids) > 1:
            for page_id in ids:
                findings.append(AuditFinding(page_id, "duplicate_daily_log_date", f"date={date} duplicate_count={len(ids)}", date))
    return {"page_count": len(pages), "finding_count": len(findings), "findings": [asdict(f) for f in findings]}


def markdown_summary(report: Mapping[str, Any]) -> str:
    lines = ["# Daily Log integrity audit", "", f"- page_count: {report.get('page_count', 0)}", f"- finding_count: {report.get('finding_count', 0)}", "", "| code | page_id | official_date | message |", "|---|---|---|---|"]
    for f in report.get("findings", []):
        if isinstance(f, Mapping):
            lines.append(f"| {f.get('code','')} | {f.get('page_id','')} | {f.get('official_date') or ''} | {str(f.get('message','')).replace('|','/')} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline-only Daily Log integrity auditor. It reads exported JSON and never writes to Notion.")
    ap.add_argument("--input-json", required=True, help="Offline Notion pages JSON export to audit")
    ap.add_argument("--output-json", help="Write audit report JSON to this path")
    ap.add_argument("--output-markdown", help="Write audit report Markdown summary to this path")
    args = ap.parse_args()
    with open(args.input_json, encoding="utf-8") as f:
        pages = json.load(f)
    if not isinstance(pages, list):
        raise SystemExit("--input-json must contain a JSON list of Notion pages")
    report = audit_pages(pages)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output_json:
        Path(args.output_json).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown_summary(report), encoding="utf-8")


if __name__ == "__main__":
    main()
