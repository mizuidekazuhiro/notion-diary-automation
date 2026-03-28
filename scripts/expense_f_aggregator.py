from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

NOTION_VERSION = "2022-06-28"
JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class ExpenseFAggregate:
    available: bool
    count: int
    total: float
    merchants: list[str]
    categories: list[str]
    first_time: Optional[str]
    last_time: Optional[str]
    data_status: str
    debug_summary: dict[str, Any]
    skip_reason: Optional[str] = None


def aggregate_daily_expense_f(target_date: str) -> ExpenseFAggregate:
    token = os.getenv("NOTION_TOKEN", "").strip()
    db_id = os.getenv("EXPENSES_DB_ID", "").strip()
    missing_env: list[str] = []
    if not token:
        missing_env.append("NOTION_TOKEN")
    if not db_id:
        missing_env.append("EXPENSES_DB_ID")
    if missing_env:
        return ExpenseFAggregate(
            False,
            0,
            0.0,
            [],
            [],
            None,
            None,
            "unavailable",
            {"reason": "missing_env", "missing": missing_env},
            "expenses_data_unavailable",
        )

    f_prop = os.getenv("EXPENSE_F_PROP", "F")
    date_prop = os.getenv("EXPENSE_DATE_PROP", "Date")
    recv_prop = os.getenv("EXPENSE_RECEIVED_AT_PROP", "Received At")
    merchant_prop = os.getenv("EXPENSE_MERCHANT_PROP", "Merchant")
    amount_prop = os.getenv("EXPENSE_AMOUNT_PROP", "Amount")

    day = datetime.fromisoformat(target_date).replace(tzinfo=JST)
    day_end = day + timedelta(days=1)
    payload = {
        "filter": {
            "or": [
                {
                    "and": [
                        {"property": recv_prop, "date": {"on_or_after": day.isoformat()}},
                        {"property": recv_prop, "date": {"before": day_end.isoformat()}},
                        {"property": f_prop, "checkbox": {"equals": True}},
                    ]
                },
                {
                    "and": [
                        {"property": recv_prop, "date": {"is_empty": True}},
                        {"property": date_prop, "date": {"equals": target_date}},
                        {"property": f_prop, "checkbox": {"equals": True}},
                    ]
                },
            ]
        },
        "sorts": [{"property": recv_prop, "direction": "ascending"}],
        "page_size": 100,
    }

    try:
        pages: list[dict[str, Any]] = []
        cursor = None
        while True:
            body = dict(payload)
            if cursor:
                body["start_cursor"] = cursor
            resp = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": NOTION_VERSION,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=20,
            )
            if resp.status_code >= 400:
                return ExpenseFAggregate(False, 0, 0.0, [], [], None, None, "unavailable", {"status": resp.status_code}, "expenses_data_unavailable")
            data = resp.json()
            batch = data.get("results", [])
            pages.extend(batch)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        total = 0.0
        merchants: list[str] = []
        categories: list[str] = []
        times: list[str] = []
        for page in pages:
            props = page.get("properties", {})
            total += _parse_number(props.get(amount_prop)) or 0.0
            merchant = _parse_rich_text(props.get(merchant_prop)) or "Unknown"
            merchants.append(merchant)
            dt = _parse_date_start(props.get(recv_prop)) or _parse_date_start(props.get(date_prop))
            if dt:
                times.append(dt)

        uniq_merchants = sorted({m for m in merchants if m})
        uniq_categories = sorted({c for c in categories if c})
        return ExpenseFAggregate(
            available=True,
            count=len(pages),
            total=round(total, 2),
            merchants=uniq_merchants,
            categories=uniq_categories,
            first_time=times[0] if times else None,
            last_time=times[-1] if times else None,
            data_status="ok",
            debug_summary={
                "count": len(pages),
                "merchant_count": len(uniq_merchants),
                "resolved_props": {
                    "f": f_prop,
                    "date": date_prop,
                    "received_at": recv_prop,
                    "merchant": merchant_prop,
                    "amount": amount_prop,
                },
                "category_unused": True,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ExpenseFAggregate(False, 0, 0.0, [], [], None, None, "unavailable", {"error": str(exc)}, "expenses_data_unavailable")


def _parse_rich_text(prop: dict[str, Any] | None) -> Optional[str]:
    if not prop:
        return None
    ptype = prop.get("type")
    if ptype == "title":
        text = "".join(x.get("plain_text", "") for x in prop.get("title", []))
        return text.strip() or None
    if ptype == "rich_text":
        text = "".join(x.get("plain_text", "") for x in prop.get("rich_text", []))
        return text.strip() or None
    if ptype == "select":
        sel = prop.get("select")
        return (sel or {}).get("name")
    return None


def _parse_number(prop: dict[str, Any] | None) -> Optional[float]:
    if not prop:
        return None
    if prop.get("type") == "number":
        n = prop.get("number")
        return float(n) if n is not None else None
    return None


def _parse_category(prop: dict[str, Any] | None) -> Optional[str]:
    if not prop:
        return None
    ptype = prop.get("type")
    if ptype == "select":
        sel = prop.get("select")
        return (sel or {}).get("name")
    if ptype == "multi_select":
        items = prop.get("multi_select", [])
        names = [item.get("name") for item in items if isinstance(item, dict) and item.get("name")]
        return ",".join(names) if names else None
    if ptype in {"rich_text", "title"}:
        return _parse_rich_text(prop)
    return None


def _parse_date_start(prop: dict[str, Any] | None) -> Optional[str]:
    if not prop or prop.get("type") != "date":
        return None
    date_obj = prop.get("date")
    if not isinstance(date_obj, dict):
        return None
    start = date_obj.get("start")
    if not isinstance(start, str):
        return None
    return start
