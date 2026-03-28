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
    aggregates = aggregate_expense_f_for_dates([target_date])
    return aggregates.get(
        target_date,
        ExpenseFAggregate(
            available=False,
            count=0,
            total=0.0,
            merchants=[],
            categories=[],
            first_time=None,
            last_time=None,
            data_status="unavailable",
            debug_summary={"reason": "target_date_not_found"},
            skip_reason="expenses_data_unavailable",
        ),
    )


def aggregate_expense_f_for_dates(target_dates: list[str]) -> dict[str, ExpenseFAggregate]:
    if not target_dates:
        return {}

    token = os.getenv("NOTION_TOKEN", "").strip()
    db_id = os.getenv("EXPENSES_DB_ID", "").strip()
    missing_env: list[str] = []
    if not token:
        missing_env.append("NOTION_TOKEN")
    if not db_id:
        missing_env.append("EXPENSES_DB_ID")
    if missing_env:
        unavailable = ExpenseFAggregate(
            available=False,
            count=0,
            total=0.0,
            merchants=[],
            categories=[],
            first_time=None,
            last_time=None,
            data_status="unavailable",
            debug_summary={"reason": "missing_env", "missing": missing_env},
            skip_reason="expenses_data_unavailable",
        )
        return {target_date: unavailable for target_date in target_dates}

    f_prop = os.getenv("EXPENSE_F_PROP", "F")
    date_prop = os.getenv("EXPENSE_DATE_PROP", "Date")
    recv_prop = os.getenv("EXPENSE_RECEIVED_AT_PROP", "Received At")
    merchant_prop = os.getenv("EXPENSE_MERCHANT_PROP", "Merchant")
    amount_prop = os.getenv("EXPENSE_AMOUNT_PROP", "Amount")
    category_prop = os.getenv("EXPENSE_CATEGORY_PROP", "Category")

    target_date_set = {item for item in target_dates}
    start_day = min(datetime.fromisoformat(item).replace(tzinfo=JST) for item in target_date_set)
    end_day = max(datetime.fromisoformat(item).replace(tzinfo=JST) for item in target_date_set) + timedelta(days=1)
    payload = {
        "filter": {
            "or": [
                {
                    "and": [
                        {"property": recv_prop, "date": {"on_or_after": start_day.isoformat()}},
                        {"property": recv_prop, "date": {"before": end_day.isoformat()}},
                        {"property": f_prop, "checkbox": {"equals": True}},
                    ]
                },
                {
                    "and": [
                        {"property": recv_prop, "date": {"is_empty": True}},
                        {"property": date_prop, "date": {"on_or_after": start_day.strftime("%Y-%m-%d")}},
                        {"property": date_prop, "date": {"on_or_before": (end_day - timedelta(days=1)).strftime("%Y-%m-%d")}},
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
                unavailable = ExpenseFAggregate(
                    False, 0, 0.0, [], [], None, None, "unavailable", {"status": resp.status_code}, "expenses_data_unavailable"
                )
                return {target_date: unavailable for target_date in target_dates}
            data = resp.json()
            batch = data.get("results", [])
            pages.extend(batch)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        grouped: dict[str, dict[str, Any]] = {
            target_date: {"total": 0.0, "merchants": [], "categories": [], "times": [], "count": 0}
            for target_date in target_date_set
        }
        for page in pages:
            props = page.get("properties", {})
            day_key = _resolve_target_date(props.get(recv_prop), props.get(date_prop))
            if not day_key or day_key not in grouped:
                continue
            grouped[day_key]["count"] += 1
            grouped[day_key]["total"] += _parse_number(props.get(amount_prop)) or 0.0
            merchant = _parse_rich_text(props.get(merchant_prop)) or "Unknown"
            grouped[day_key]["merchants"].append(merchant)
            category = _parse_category(props.get(category_prop))
            if category:
                grouped[day_key]["categories"].append(category)
            dt = _parse_date_start(props.get(recv_prop)) or _parse_date_start(props.get(date_prop))
            if dt:
                grouped[day_key]["times"].append(dt)

        result: dict[str, ExpenseFAggregate] = {}
        for target_date in target_dates:
            item = grouped.get(target_date, {"count": 0, "total": 0.0, "merchants": [], "categories": [], "times": []})
            uniq_merchants = sorted({m for m in item["merchants"] if m})
            uniq_categories = sorted({c for c in item["categories"] if c})
            times = item["times"]
            result[target_date] = ExpenseFAggregate(
                available=True,
                count=int(item["count"]),
                total=round(float(item["total"]), 2),
                merchants=uniq_merchants,
                categories=uniq_categories,
                first_time=times[0] if times else None,
                last_time=times[-1] if times else None,
                data_status="ok",
                debug_summary={
                    "count": int(item["count"]),
                    "merchant_count": len(uniq_merchants),
                    "resolved_props": {
                        "f": f_prop,
                        "date": date_prop,
                        "received_at": recv_prop,
                        "merchant": merchant_prop,
                        "amount": amount_prop,
                        "category": category_prop,
                    },
                    "source": "expenses_db_direct",
                    "query_range_start": start_day.strftime("%Y-%m-%d"),
                    "query_range_end": (end_day - timedelta(days=1)).strftime("%Y-%m-%d"),
                },
            )
        return result
    except Exception as exc:  # noqa: BLE001
        unavailable = ExpenseFAggregate(False, 0, 0.0, [], [], None, None, "unavailable", {"error": str(exc)}, "expenses_data_unavailable")
        return {target_date: unavailable for target_date in target_dates}


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


def _resolve_target_date(received_at_prop: dict[str, Any] | None, date_prop: dict[str, Any] | None) -> Optional[str]:
    received = _parse_date_start(received_at_prop)
    if received:
        try:
            normalized = received.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).astimezone(JST).strftime("%Y-%m-%d")
        except ValueError:
            return received[:10]
    day = _parse_date_start(date_prop)
    return day[:10] if day else None
