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
            first_time=None,
            last_time=None,
            data_status="query_failed",
            debug_summary={"reason": "target_date_not_found"},
            skip_reason="expenses_data_unavailable",
        ),
    )


def _env_or_none(key: str) -> Optional[str]:
    value = os.getenv(key, "").strip()
    return value or None


def _resolve_prop_name(*, env_value: Optional[str], aliases: list[str], schema: dict[str, Any]) -> tuple[Optional[str], dict[str, Any]]:
    if env_value:
        return env_value, {"source": "env", "value": env_value, "resolved": True}
    lower_aliases = {x.lower() for x in aliases}
    for prop_name in schema.keys():
        if prop_name.lower() in lower_aliases:
            return prop_name, {"source": "schema_alias", "value": prop_name, "resolved": True}
    return None, {"source": "schema_alias", "value": None, "resolved": False, "aliases": aliases}


def _fetch_schema(*, token: str, db_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        resp = requests.get(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            return {}, {"ok": False, "status_code": resp.status_code, "reason": "schema_fetch_failed"}
        payload = resp.json()
        props = payload.get("properties") if isinstance(payload, dict) else {}
        return props if isinstance(props, dict) else {}, {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {}, {"ok": False, "reason": "schema_fetch_exception", "error": str(exc)}


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
            first_time=None,
            last_time=None,
            data_status="query_failed",
            debug_summary={"reason": "missing_env", "missing": missing_env},
            skip_reason="expenses_data_unavailable",
        )
        return {target_date: unavailable for target_date in target_dates}

    schema, schema_debug = _fetch_schema(token=token, db_id=db_id)
    resolved_props = {
        "f": _resolve_prop_name(env_value=_env_or_none("EXPENSE_F_PROP"), aliases=["F", "f", "F判定"], schema=schema),
        "date": _resolve_prop_name(env_value=_env_or_none("EXPENSE_DATE_PROP"), aliases=["Date", "日付"], schema=schema),
        "received_at": _resolve_prop_name(env_value=_env_or_none("EXPENSE_RECEIVED_AT_PROP"), aliases=["Received At", "受領日時", "Timestamp", "Created time"], schema=schema),
        "merchant": _resolve_prop_name(env_value=_env_or_none("EXPENSE_MERCHANT_PROP"), aliases=["Merchant", "店名", "支出先"], schema=schema),
        "amount": _resolve_prop_name(env_value=_env_or_none("EXPENSE_AMOUNT_PROP"), aliases=["Amount", "金額"], schema=schema),
    }
    resolved_names = {k: v[0] for k, v in resolved_props.items()}
    required_fields = ["f", "merchant", "amount"]
    unresolved_required = [k for k in required_fields if not resolved_names.get(k)]
    if not (resolved_names["received_at"] or resolved_names["date"]):
        unresolved_required.append("date_or_received_at")
    if unresolved_required:
        unavailable = ExpenseFAggregate(
            available=False,
            count=0,
            total=0.0,
            merchants=[],
            first_time=None,
            last_time=None,
            data_status="schema_unresolved",
            debug_summary={
                "schema_fetch": schema_debug,
                "resolved_props": {k: {"resolved_name": v[0], **v[1]} for k, v in resolved_props.items()},
                "unresolved_required": unresolved_required,
            },
            skip_reason="expenses_data_unavailable",
        )
        return {target_date: unavailable for target_date in target_dates}

    f_prop = resolved_names["f"]
    date_prop = resolved_names["date"]
    recv_prop = resolved_names["received_at"]
    merchant_prop = resolved_names["merchant"]
    amount_prop = resolved_names["amount"]

    target_date_set = {item for item in target_dates}
    start_day = min(datetime.fromisoformat(item).replace(tzinfo=JST) for item in target_date_set)
    end_day = max(datetime.fromisoformat(item).replace(tzinfo=JST) for item in target_date_set) + timedelta(days=1)

    time_or_blocks: list[dict[str, Any]] = []
    if recv_prop:
        time_or_blocks.append(
            {
                "and": [
                    {"property": recv_prop, "date": {"on_or_after": start_day.isoformat()}},
                    {"property": recv_prop, "date": {"before": end_day.isoformat()}},
                ]
            }
        )
    if date_prop:
        time_or_blocks.append(
            {
                "and": [
                    {"property": date_prop, "date": {"on_or_after": start_day.strftime("%Y-%m-%d")}},
                    {"property": date_prop, "date": {"on_or_before": (end_day - timedelta(days=1)).strftime("%Y-%m-%d")}},
                ]
            }
        )
    payload = {
        "filter": {
            "and": [
                {"property": f_prop, "checkbox": {"equals": True}},
                {"or": time_or_blocks},
            ]
        },
        "sorts": ([{"property": recv_prop, "direction": "ascending"}] if recv_prop else []),
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
                    False,
                    0,
                    0.0,
                    [],
                    None,
                    None,
                    "query_failed",
                    {
                        "status": resp.status_code,
                        "resolved_props": {k: {"resolved_name": v[0], **v[1]} for k, v in resolved_props.items()},
                    },
                    "expenses_data_unavailable",
                )
                return {target_date: unavailable for target_date in target_dates}
            data = resp.json()
            batch = data.get("results", [])
            pages.extend(batch)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        grouped: dict[str, dict[str, Any]] = {
            target_date: {"total": 0.0, "merchants": [], "times": [], "count": 0}
            for target_date in target_date_set
        }
        matching_rows = 0
        for page in pages:
            props = page.get("properties", {})
            day_key = _resolve_target_date(props.get(recv_prop) if recv_prop else None, props.get(date_prop) if date_prop else None)
            if not day_key or day_key not in grouped:
                continue
            matching_rows += 1
            grouped[day_key]["count"] += 1
            grouped[day_key]["total"] += _parse_number(props.get(amount_prop) if amount_prop else None) or 0.0
            merchant = _parse_rich_text(props.get(merchant_prop) if merchant_prop else None) or "Unknown"
            grouped[day_key]["merchants"].append(merchant)
            dt = _parse_date_start(props.get(recv_prop) if recv_prop else None) or _parse_date_start(props.get(date_prop) if date_prop else None)
            if dt:
                grouped[day_key]["times"].append(dt)

        result: dict[str, ExpenseFAggregate] = {}
        for target_date in target_dates:
            item = grouped.get(target_date, {"count": 0, "total": 0.0, "merchants": [], "times": []})
            uniq_merchants = sorted({m for m in item["merchants"] if m})
            times = item["times"]
            count_value = int(item["count"])
            if not pages:
                status = "no_matching_rows"
            elif matching_rows == 0:
                status = "no_matching_rows"
            elif count_value == 0:
                status = "matched_zero"
            else:
                status = "ok"
            result[target_date] = ExpenseFAggregate(
                available=True,
                count=count_value,
                total=round(float(item["total"]), 2),
                merchants=uniq_merchants,
                first_time=times[0] if times else None,
                last_time=times[-1] if times else None,
                data_status=status,
                debug_summary={
                    "count": count_value,
                    "merchant_count": len(uniq_merchants),
                    "resolved_props": {k: {"resolved_name": v[0], **v[1]} for k, v in resolved_props.items()},
                    "schema_fetch": schema_debug,
                    "source": "expenses_db_direct",
                    "query_range_start": start_day.strftime("%Y-%m-%d"),
                    "query_range_end": (end_day - timedelta(days=1)).strftime("%Y-%m-%d"),
                    "matching_rows": matching_rows,
                    "all_rows": len(pages),
                },
            )
        return result
    except Exception as exc:  # noqa: BLE001
        unavailable = ExpenseFAggregate(
            False,
            0,
            0.0,
            [],
            None,
            None,
            "query_failed",
            {"error": str(exc), "resolved_props": {k: {"resolved_name": v[0], **v[1]} for k, v in resolved_props.items()}},
            "expenses_data_unavailable",
        )
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
