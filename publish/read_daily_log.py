from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlencode

from ingest.http_client import fetch_json


@dataclass(frozen=True)
class ExpenseItem:
    title: str
    amount: float
    url: str


@dataclass(frozen=True)
class ExpenseSummary:
    total: float
    count: int
    top: List[ExpenseItem]
    remaining: int


@dataclass(frozen=True)
class DoneTaskDetail:
    title: str
    done_date: Optional[str]
    event_date: Optional[str]


@dataclass(frozen=True)
class DailyLogSummary:
    target_date: str
    date: Optional[str]
    target_date_value: Optional[str]
    page_id: str
    title: str
    summary_text: str
    summary_html: str
    mail_id: str
    source: Optional[str]
    diary: Optional[str]
    meal_summary: Optional[str]
    meal_photos: List[str]
    place: Optional[str]
    activity_summary: Optional[str]
    done_count: Optional[int]
    done_tasks: List[str]
    done_tasks_detail: List[DoneTaskDetail]
    drop_count: Optional[int]
    drop_tasks: List[str]
    kcal: Optional[float]
    protein: Optional[float]
    fat: Optional[float]
    carb: Optional[float]
    expenses_total: Optional[float]
    expenses: ExpenseSummary
    location_summary: Optional[str]
    mood: Optional[str]
    notes: Optional[str]
    weight: Optional[float]
    page_url: Optional[str]
    diary_notification_sent: Optional[bool]


def read_daily_log(
    *, daily_log_read_url: str, target_date: str, bearer_token: Optional[str]
) -> Optional[DailyLogSummary]:
    url = f"{daily_log_read_url}?{urlencode({'date': target_date})}"
    payload = fetch_json(url, bearer_token)
    if not payload.get("found"):
        return None

    expenses_payload = payload.get("expenses", {}) if isinstance(payload, dict) else {}
    top_entries: List[ExpenseItem] = []
    if isinstance(expenses_payload, dict):
        top_payload = expenses_payload.get("top", [])
        if isinstance(top_payload, list):
            for item in top_payload:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                amount = float(item.get("amount") or 0)
                url = str(item.get("url") or "")
                top_entries.append(ExpenseItem(title=title, amount=amount, url=url))

    done_tasks_detail_payload = payload.get("done_tasks_detail", []) or []
    done_tasks_detail: List[DoneTaskDetail] = []
    if isinstance(done_tasks_detail_payload, list):
        for item in done_tasks_detail_payload:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            done_tasks_detail.append(
                DoneTaskDetail(
                    title=title,
                    done_date=item.get("done_date"),
                    event_date=item.get("event_date"),
                )
            )

    expenses_summary = ExpenseSummary(
        total=float(expenses_payload.get("total") or 0),
        count=int(expenses_payload.get("count") or 0),
        top=top_entries,
        remaining=int(expenses_payload.get("remaining") or 0),
    )

    return DailyLogSummary(
        target_date=payload.get("target_date", target_date),
        date=payload.get("date"),
        target_date_value=payload.get("target_date_value"),
        page_id=payload.get("page_id", ""),
        title=payload.get("title", ""),
        summary_text=payload.get("summary_text", ""),
        summary_html=payload.get("summary_html", ""),
        mail_id=payload.get("mail_id", ""),
        source=payload.get("source"),
        diary=payload.get("diary"),
        meal_summary=payload.get("meal_summary"),
        meal_photos=payload.get("meal_photos", []) or [],
        place=payload.get("place"),
        activity_summary=payload.get("activity_summary"),
        done_count=payload.get("done_count"),
        done_tasks=payload.get("done_tasks", []) or [],
        done_tasks_detail=done_tasks_detail,
        drop_count=payload.get("drop_count"),
        drop_tasks=payload.get("drop_tasks", []) or [],
        kcal=payload.get("kcal"),
        protein=payload.get("protein"),
        fat=payload.get("fat"),
        carb=payload.get("carb"),
        expenses_total=payload.get("expenses_total"),
        expenses=expenses_summary,
        location_summary=payload.get("location_summary"),
        mood=payload.get("mood"),
        notes=payload.get("notes"),
        weight=payload.get("weight"),
        page_url=payload.get("page_url"),
        diary_notification_sent=payload.get("diary_notification_sent"),
    )
