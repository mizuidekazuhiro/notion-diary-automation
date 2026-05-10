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
    return aggregate_expense_f_for_dates([target_date]).get(
        target_date,
        ExpenseFAggregate(False, 0, 0.0, [], None, None, "query_failed", {"reason": "target_date_not_found"}, "expenses_data_unavailable"),
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


def _is_date_prop(schema: dict[str, Any], prop_name: Optional[str]) -> bool:
    if not prop_name:
        return False
    prop = schema.get(prop_name)
    return isinstance(prop, dict) and prop.get("type") == "date"


def _fetch_schema(*, token: str, db_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        resp = requests.get(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
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
    if not token or not db_id:
        unavailable = ExpenseFAggregate(False, 0, 0.0, [], None, None, "query_failed", {"reason": "missing_env", "missing": [k for k, v in {"NOTION_TOKEN": token, "EXPENSES_DB_ID": db_id}.items() if not v]}, "expenses_data_unavailable")
        return {d: unavailable for d in target_dates}

    schema, schema_debug = _fetch_schema(token=token, db_id=db_id)
    resolved_props = {
        "f": _resolve_prop_name(env_value=_env_or_none("EXPENSE_F_PROP"), aliases=["F", "f", "F判定"], schema=schema),
        "merchant": _resolve_prop_name(env_value=_env_or_none("EXPENSE_MERCHANT_PROP"), aliases=["Merchant", "店名", "支出先"], schema=schema),
        "amount": _resolve_prop_name(env_value=_env_or_none("EXPENSE_AMOUNT_PROP"), aliases=["Amount", "金額"], schema=schema),
        "category": _resolve_prop_name(env_value=_env_or_none("EXPENSE_CATEGORY_PROP"), aliases=["Category", "カテゴリ", "費目"], schema=schema),
        "date": _resolve_prop_name(env_value=_env_or_none("EXPENSE_DATE_PROP"), aliases=["Date", "日付"], schema=schema),
        "received_at": _resolve_prop_name(env_value=_env_or_none("EXPENSE_RECEIVED_AT_PROP"), aliases=["Received At", "受領日時", "Timestamp"], schema=schema),
    }
    names = {k: v[0] for k, v in resolved_props.items()}
    unresolved = [k for k in ["f", "merchant", "amount"] if not names.get(k)]
    if unresolved:
        unavailable = ExpenseFAggregate(False, 0, 0.0, [], None, None, "schema_unresolved", {"schema_fetch": schema_debug, "resolved_props": {k: {"resolved_name": v[0], **v[1]} for k, v in resolved_props.items()}, "unresolved_required": unresolved}, "expenses_data_unavailable")
        return {d: unavailable for d in target_dates}

    target_set = set(target_dates)
    start_day = min(datetime.fromisoformat(d).replace(tzinfo=JST) for d in target_set)
    end_day = max(datetime.fromisoformat(d).replace(tzinfo=JST) for d in target_set) + timedelta(days=1)
    if _is_date_prop(schema, names.get("date")):
        date_filter = [
            {"property": names["date"], "date": {"on_or_after": start_day.date().isoformat()}},
            {"property": names["date"], "date": {"before": end_day.date().isoformat()}},
        ]
        filter_strategy = "expense_date_prop"
        query_time_source = "expense_date"
    elif _is_date_prop(schema, names.get("received_at")):
        date_filter = [
            {"property": names["received_at"], "date": {"on_or_after": start_day.date().isoformat()}},
            {"property": names["received_at"], "date": {"before": end_day.date().isoformat()}},
        ]
        filter_strategy = "received_at_prop"
        query_time_source = "received_at"
    else:
        date_filter = [
            {"timestamp": "created_time", "created_time": {"on_or_after": start_day.astimezone().isoformat()}},
            {"timestamp": "created_time", "created_time": {"before": end_day.astimezone().isoformat()}},
        ]
        filter_strategy = "created_time_fallback"
        query_time_source = "created_time"
    family_prop = _resolve_prop_name(env_value=_env_or_none("EXPENSE_FAMILY_CARD_PROP"), aliases=["FamilyCard"], schema=schema)[0]
    filter_terms = [{"property": names["f"], "checkbox": {"equals": True}}, *date_filter]
    if family_prop:
        filter_terms.append({"or": [{"property": family_prop, "checkbox": {"equals": False}}, {"property": family_prop, "checkbox": {"is_empty": True}}]})
    filter_payload = {"and": filter_terms}
    payload = {"filter": filter_payload, "sorts": [{"timestamp": "created_time", "direction": "ascending"}], "page_size": 100}

    try:
        pages: list[dict[str, Any]] = []
        cursor = None
        while True:
            body = dict(payload)
            if cursor:
                body["start_cursor"] = cursor
            resp = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"},
                json=body,
                timeout=20,
            )
            if resp.status_code >= 400:
                unavailable = ExpenseFAggregate(False, 0, 0.0, [], None, None, "query_failed", {"status": resp.status_code, "filter_strategy": filter_strategy, "resolved_props": {k: {"resolved_name": v[0], **v[1]} for k, v in resolved_props.items()}, "query_exception_class": "HTTPError", "query_exception_message": f"status_code={resp.status_code}"}, "expenses_data_unavailable")
                return {d: unavailable for d in target_dates}
            data = resp.json()
            pages.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        grouped = {d: {"total": 0.0, "merchants": [], "times": [], "count": 0} for d in target_set}
        for page in pages:
            props = page.get("properties", {})
            created_time = str(page.get("created_time") or "")
            day_key = _resolve_target_date(created_time)
            if query_time_source == "expense_date":
                day_key = _parse_date_prop(props.get(names["date"])) or day_key
            elif query_time_source == "received_at":
                day_key = _parse_date_prop(props.get(names["received_at"])) or day_key
            if not day_key or day_key not in grouped:
                continue
            grouped[day_key]["count"] += 1
            grouped[day_key]["total"] += _parse_number(props.get(names["amount"])) or 0.0
            grouped[day_key]["merchants"].append(_parse_rich_text(props.get(names["merchant"])) or "Unknown")
            grouped[day_key]["times"].append(created_time)

        result: dict[str, ExpenseFAggregate] = {}
        for target_date in target_dates:
            row = grouped.get(target_date, {"count": 0, "total": 0.0, "merchants": [], "times": []})
            times = sorted([t for t in row["times"] if t])
            count_value = int(row["count"])
            status = "ok" if count_value > 0 else "no_results"
            result[target_date] = ExpenseFAggregate(
                available=True,
                count=count_value,
                total=round(float(row["total"]), 2),
                merchants=sorted({m for m in row["merchants"] if m}),
                first_time=times[0] if times else None,
                last_time=times[-1] if times else None,
                data_status=status,
                debug_summary={
                    "resolved_props": {k: {"resolved_name": v[0], **v[1]} for k, v in resolved_props.items()},
                    "created_time_source": query_time_source,
                    "date_window_start": start_day.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "date_window_end": end_day.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "filter_strategy": filter_strategy,
                    "query_exception_class": None,
                    "query_exception_message": None,
                    "matched_count": count_value,
                    "total_amount": round(float(row["total"]), 2),
                    "all_rows": len(pages),
                    "schema_fetch": schema_debug,
                },
            )
        return result
    except Exception as exc:  # noqa: BLE001
        unavailable = ExpenseFAggregate(False, 0, 0.0, [], None, None, "query_failed", {"query_exception_class": exc.__class__.__name__, "query_exception_message": str(exc), "filter_strategy": filter_strategy, "resolved_props": {k: {"resolved_name": v[0], **v[1]} for k, v in resolved_props.items()}}, "expenses_data_unavailable")
        return {d: unavailable for d in target_dates}


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
    if prop and prop.get("type") == "number":
        n = prop.get("number")
        return float(n) if n is not None else None
    return None


def _parse_date_prop(prop: dict[str, Any] | None) -> Optional[str]:
    if not prop or prop.get("type") != "date":
        return None
    date_obj = prop.get("date") or {}
    raw = str(date_obj.get("start") or "").strip()
    if not raw:
        return None
    return raw[:10]


def _resolve_target_date(created_time: str) -> Optional[str]:
    if not created_time:
        return None
    try:
        return datetime.fromisoformat(created_time.replace("Z", "+00:00")).astimezone(JST).strftime("%Y-%m-%d")
    except ValueError:
        return created_time[:10]
