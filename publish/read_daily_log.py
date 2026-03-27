from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional
from urllib.parse import urlencode

from ingest.http_client import fetch_json
from scripts.sleep_utils import format_sleep_duration_text, resolve_sleep_duration_minutes


def _safe_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _safe_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    return None


def _safe_string_list(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped:
            items.append(stripped)
    return items


def _safe_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _normalize_property_key(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch not in {" ", "_", "-"})


DAILY_LOG_SLEEP_PROPERTY_ALIASES = {
    "sleep_start": ("Sleep Start",),
    "sleep_end": ("Sleep End",),
    "sleep_duration_min": ("Sleep Duration",),
    "sleep_score": ("Sleep Score",),
    "sleep_source": ("Sleep Source",),
    "sleep_heart_rate": ("Sleep Heart Rate",),
    "deep_duration_min": ("Deep Duration",),
    "rem_duration_min": ("REM Duration",),
    "readiness_stars": ("Readiness Stars",),
    "readiness_hrv": ("Readiness HRV",),
    "readiness_bpm": ("Readiness BPM",),
    "baseline_hrv": ("Baseline HRV",),
    "baseline_waking_bpm": ("Baseline Waking BPM",),
    "sleep_analysis_jp": ("Sleep Analysis JP", "Sleep Analysis"),
    "today_condition_forecast_jp": ("Today Condition Forecast JP", "Today Condition Forecast"),
}


def _get_case_insensitive_value(payload: Mapping[str, Any], *candidate_keys: str) -> object:
    normalized_payload: dict[str, list[str]] = {}
    for key in payload.keys():
        if not isinstance(key, str):
            continue
        normalized_payload.setdefault(_normalize_property_key(key), []).append(key)

    for candidate in candidate_keys:
        if candidate in payload:
            return payload[candidate]
        matches = normalized_payload.get(_normalize_property_key(candidate), [])
        if len(matches) > 1:
            return None
        if len(matches) == 1:
            return payload.get(matches[0])
    return None


def _get_sleep_value(payload: Mapping[str, Any], internal_name: str) -> object:
    aliases: Iterable[str] = DAILY_LOG_SLEEP_PROPERTY_ALIASES.get(internal_name, ())
    candidates = [internal_name, *aliases]
    return _get_case_insensitive_value(payload, *candidates)


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
    sleep_start: Optional[str]
    sleep_end: Optional[str]
    sleep_duration_min: Optional[float]
    resolved_sleep_duration_min: Optional[float]
    resolved_sleep_duration_hours: Optional[float]
    resolved_sleep_duration_text: Optional[str]
    sleep_duration_source: str
    sleep_score: Optional[float]
    sleep_source: Optional[str]
    readiness_stars: Optional[float]
    readiness_hrv: Optional[float]
    readiness_bpm: Optional[float]
    baseline_hrv: Optional[float]
    baseline_waking_bpm: Optional[float]
    sleep_heart_rate: Optional[float]
    deep_duration_min: Optional[float]
    rem_duration_min: Optional[float]
    sleep_analysis_jp: Optional[str]
    today_condition_forecast_jp: Optional[str]
    today_advice: Optional[str]
    diary_input_hash: Optional[str]
    today_advice_input_hash: Optional[str]
    diary_generated_at: Optional[str]
    today_advice_generated_at: Optional[str]
    page_url: Optional[str]
    diary_notification_sent: Optional[bool]


def read_daily_log(
    *, daily_log_read_url: str, target_date: str, bearer_token: Optional[str]
) -> Optional[DailyLogSummary]:
    url = f"{daily_log_read_url}?{urlencode({'date': target_date})}"
    payload = fetch_json(url, bearer_token)
    if not payload.get("found"):
        return None

    expenses_payload = _safe_mapping(payload.get("expenses") if isinstance(payload, Mapping) else None)
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

    sleep_start = _safe_text(_get_sleep_value(payload, "sleep_start"))
    sleep_end = _safe_text(_get_sleep_value(payload, "sleep_end"))
    raw_sleep_duration_min = _safe_float(_get_sleep_value(payload, "sleep_duration_min"))
    resolved_sleep = resolve_sleep_duration_minutes(sleep_start, sleep_end, raw_sleep_duration_min)
    resolved_sleep_duration_min = resolved_sleep.resolved_sleep_duration_min
    resolved_sleep_duration_hours = (round(resolved_sleep_duration_min / 60.0, 2) if resolved_sleep_duration_min is not None else None)
    resolved_sleep_duration_text = format_sleep_duration_text(resolved_sleep_duration_min)

    return DailyLogSummary(
        target_date=_safe_text(payload.get("target_date")) or target_date,
        date=_safe_text(payload.get("date")),
        target_date_value=_safe_text(payload.get("target_date_value")),
        page_id=_safe_text(payload.get("page_id")) or "",
        title=_safe_text(payload.get("title")) or "",
        summary_text=_safe_text(payload.get("summary_text")) or "",
        summary_html=_safe_text(payload.get("summary_html")) or "",
        mail_id=_safe_text(payload.get("mail_id")) or "",
        source=_safe_text(payload.get("source")),
        diary=_safe_text(payload.get("diary")),
        meal_summary=_safe_text(payload.get("meal_summary")),
        meal_photos=_safe_string_list(payload.get("meal_photos")),
        place=_safe_text(payload.get("place")),
        activity_summary=_safe_text(payload.get("activity_summary")),
        done_count=_safe_int(payload.get("done_count")),
        done_tasks=_safe_string_list(payload.get("done_tasks")),
        done_tasks_detail=done_tasks_detail,
        drop_count=_safe_int(payload.get("drop_count")),
        drop_tasks=_safe_string_list(payload.get("drop_tasks")),
        kcal=_safe_float(payload.get("kcal")),
        protein=_safe_float(payload.get("protein")),
        fat=_safe_float(payload.get("fat")),
        carb=_safe_float(payload.get("carb")),
        expenses_total=_safe_float(payload.get("expenses_total")),
        expenses=expenses_summary,
        location_summary=_safe_text(payload.get("location_summary")),
        mood=_safe_text(payload.get("mood")),
        notes=_safe_text(payload.get("notes")),
        weight=_safe_float(payload.get("weight")),
        sleep_start=sleep_start,
        sleep_end=sleep_end,
        sleep_duration_min=raw_sleep_duration_min,
        resolved_sleep_duration_min=resolved_sleep_duration_min,
        resolved_sleep_duration_hours=resolved_sleep_duration_hours,
        resolved_sleep_duration_text=resolved_sleep_duration_text,
        sleep_duration_source=resolved_sleep.duration_source,
        sleep_score=_safe_float(_get_sleep_value(payload, "sleep_score")),
        sleep_source=_safe_text(_get_sleep_value(payload, "sleep_source")),
        readiness_stars=_safe_float(_get_sleep_value(payload, "readiness_stars")),
        readiness_hrv=_safe_float(_get_sleep_value(payload, "readiness_hrv")),
        readiness_bpm=_safe_float(_get_sleep_value(payload, "readiness_bpm")),
        baseline_hrv=_safe_float(_get_sleep_value(payload, "baseline_hrv")),
        baseline_waking_bpm=_safe_float(_get_sleep_value(payload, "baseline_waking_bpm")),
        sleep_heart_rate=_safe_float(_get_sleep_value(payload, "sleep_heart_rate")),
        deep_duration_min=_safe_float(_get_sleep_value(payload, "deep_duration_min")),
        rem_duration_min=_safe_float(_get_sleep_value(payload, "rem_duration_min")),
        sleep_analysis_jp=_safe_text(_get_sleep_value(payload, "sleep_analysis_jp")),
        today_condition_forecast_jp=_safe_text(_get_sleep_value(payload, "today_condition_forecast_jp")),
        today_advice=_safe_text(_get_case_insensitive_value(payload, "Today advice", "today_advice")),
        diary_input_hash=_safe_text(_get_case_insensitive_value(payload, "Diary Input Hash", "diary_input_hash")),
        today_advice_input_hash=_safe_text(_get_case_insensitive_value(payload, "Today Advice Input Hash", "today_advice_input_hash")),
        diary_generated_at=_safe_text(_get_case_insensitive_value(payload, "Diary Generated At", "diary_generated_at")),
        today_advice_generated_at=_safe_text(_get_case_insensitive_value(payload, "Today Advice Generated At", "today_advice_generated_at")),
        page_url=_safe_text(payload.get("page_url")),
        diary_notification_sent=_safe_bool(payload.get("diary_notification_sent")),
    )
