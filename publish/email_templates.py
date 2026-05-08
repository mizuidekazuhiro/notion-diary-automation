from __future__ import annotations

import html
from datetime import datetime
import re
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Tuple
from scripts.weather_client import build_weather_summary, WEATHER_CODE_MAP

MAX_TASK_ITEMS = 30
SECTION_ORDER = [
    "today_advice",
    "f_risk",
    "diary",
    "summary",
    "sleep",
    "expenses",
    "done",
    "drop",
    "meal",
    "weather",
]
TASK_PLACEHOLDER_VALUES = {"", "-", "—", "none", "なし", "無し", "null"}


@dataclass(frozen=True)
class TaskEntry:
    title: str
    priority: str


def _normalize_text(value: Optional[str]) -> str:
    if value is None:
        return "—"
    stripped = value.strip()
    return stripped if stripped else "—"


def _normalize_number(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "—"
    return f"{value:g}"


def _format_yen(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "—"
    return f"¥{value:g}"




def _classify_meal_photo_url(url: str) -> str:
    lowered = url.lower()
    if lowered.startswith("file://"):
        return "notion_file"
    if "dropbox.com" in lowered:
        return "dropbox"
    if lowered.startswith("https://"):
        return "https"
    if lowered.startswith("http://"):
        return "http"
    return "invalid"


def _is_renderable_image_url(url: str) -> bool:
    url_type = _classify_meal_photo_url(url)
    if url_type in {"https", "dropbox"}:
        return True
    return False
def _normalize_photo_urls(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    urls: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        url = item.strip()
        if url:
            urls.append(url)
    return urls


def _optional_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _resolve_weather_summary(payload: Mapping[str, object]) -> tuple[Optional[str], str]:
    for key in ("weather_summary", "Weather Summary"):
        value = _optional_text(payload.get(key))
        if value:
            return value, "saved_weather_summary"

    fallback = build_weather_summary(
        weather_code=_safe_int(payload.get("weather_code")),
        temp_max_c=_safe_float(payload.get("weather_temp_max_c")),
        temp_min_c=_safe_float(payload.get("weather_temp_min_c")),
        precip_probability_max=_safe_float(payload.get("weather_precip_probability_max")),
    )
    if fallback:
        return fallback, "fallback_from_raw"
    return None, "empty"


def _safe_float(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_weather_label(payload: Mapping[str, object]) -> str:
    code = _safe_int(payload.get("weather_code"))
    if code is None:
        return "—"
    return WEATHER_CODE_MAP.get(code, str(code))


def _format_sleep_clock(value: object) -> Optional[str]:
    text = _optional_text(value)
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).strftime("%H:%M")
    except ValueError:
        match = re.search(r"(\d{2}):(\d{2})", text)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
    return None


def _format_sleep_duration(value: object) -> Optional[str]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    hours, remain = divmod(minutes, 60)
    if hours <= 0:
        return f"{remain}分"
    return f"{hours}時間{remain}分"


def _format_study_duration(value: object) -> Optional[str]:
    minutes_float = _safe_float(value)
    if minutes_float is None:
        return None
    minutes = int(round(minutes_float))
    return f"{(minutes / 60.0):g}時間（{minutes}分）"


def _normalize_expenses(payload: Mapping[str, object]) -> Tuple[float, int, List[dict], int]:
    expenses_payload = payload.get("expenses")
    total = 0.0
    count = 0
    remaining = 0
    top: List[dict] = []

    if isinstance(expenses_payload, Mapping):
        total = float(expenses_payload.get("total") or 0)
        count = int(expenses_payload.get("count") or 0)
        remaining = int(expenses_payload.get("remaining") or 0)
        top_payload = expenses_payload.get("top", [])
        if isinstance(top_payload, list):
            for item in top_payload:
                if not isinstance(item, Mapping):
                    continue
                title = str(item.get("title") or "Untitled")
                amount = float(item.get("amount") or 0)
                url = str(item.get("url") or "")
                top.append({"title": title, "amount": amount, "url": url})
    else:
        total = float(payload.get("expenses_total") or 0)

    return total, count, top, remaining


def _parse_task_items(summary_text: str) -> Tuple[List[TaskEntry], List[TaskEntry]]:
    done_items: List[TaskEntry] = []
    drop_items: List[TaskEntry] = []
    current: Optional[str] = None
    if not summary_text:
        return done_items, drop_items

    priority_pattern = re.compile(
        r"^(?P<title>.*?)(?:\s*\(Priority:\s*(?P<priority>[^)]+)\))?$"
    )

    for raw_line in summary_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("🎉"):
            current = "done"
            continue
        if line.startswith("🧹"):
            current = "drop"
            continue
        if not line.startswith("-"):
            continue

        if current not in {"done", "drop"}:
            continue

        item_text = line[1:].strip()
        normalized_item = item_text.strip().lower()
        if normalized_item in TASK_PLACEHOLDER_VALUES:
            continue
        match = priority_pattern.match(item_text)
        if not match:
            title, priority = item_text, "-"
        else:
            title = (match.group("title") or "").strip()
            priority = (match.group("priority") or "-").strip()
        entry = TaskEntry(title=title or "(No title)", priority=priority or "-")
        if current == "done":
            done_items.append(entry)
        else:
            drop_items.append(entry)
    return done_items, drop_items


def _limit_items(items: List[TaskEntry]) -> Tuple[List[TaskEntry], int]:
    if len(items) <= MAX_TASK_ITEMS:
        return items, 0
    return items[:MAX_TASK_ITEMS], len(items) - MAX_TASK_ITEMS


def _render_priority_badge(priority: str) -> str:
    normalized = priority.strip().lower()
    color_map = {
        "high": ("#fee2e2", "#991b1b"),
        "mid": ("#fef3c7", "#92400e"),
        "medium": ("#fef3c7", "#92400e"),
        "low": ("#d1fae5", "#065f46"),
        "-": ("#e5e7eb", "#374151"),
        "": ("#e5e7eb", "#374151"),
    }
    background, text = color_map.get(normalized, ("#e5e7eb", "#374151"))
    label = html.escape(priority or "-")
    return (
        f"<span style=\"display: inline-block; padding: 2px 8px; "
        f"border-radius: 999px; font-size: 12px; background: {background}; "
        f"color: {text}; font-weight: 600; white-space: nowrap;\">{label}</span>"
    )


def _render_task_rows(items: List[TaskEntry]) -> str:
    if not items:
        return (
            "<tr>"
            "<td style=\"padding: 8px 0; color: #9ca3af; font-size: 14px;\">—</td>"
            "<td style=\"padding: 8px 0;\"></td>"
            "</tr>"
        )

    rows = []
    for item in items:
        title = html.escape(item.title)
        badge = _render_priority_badge(item.priority)
        rows.append(
            "<tr>"
            f"<td style=\"padding: 8px 0; font-size: 14px; color: #111827;\">{title}</td>"
            f"<td align=\"right\" style=\"padding: 8px 0;\">{badge}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_more_row(remaining: int) -> str:
    if remaining <= 0:
        return ""
    return (
        "<tr>"
        f"<td colspan=\"2\" style=\"padding: 8px 0; font-size: 13px; color: #6b7280;\">"
        f"...and {remaining} more"
        "</td>"
        "</tr>"
    )


def _resolve_count(payload: Mapping[str, object], key: str, fallback_count: int) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        return fallback_count
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback_count


def render_daily_log_html(payload: Mapping[str, object]) -> str:
    target_date = str(payload.get("target_date") or "")
    run_id = str(payload.get("run_id") or payload.get("mail_id") or "")
    summary_text = str(payload.get("summary_text") or "")

    done_items, drop_items = _parse_task_items(summary_text)
    done_count_display = _resolve_count(payload, "done_count", len(done_items))
    drop_count_display = _resolve_count(payload, "drop_count", len(drop_items))
    done_visible, done_more = _limit_items(done_items)
    drop_visible, drop_more = _limit_items(drop_items)

    diary = _normalize_text(payload.get("diary") if isinstance(payload, Mapping) else None)
    meal_summary = _normalize_text(
        payload.get("meal_summary") if isinstance(payload, Mapping) else None
    )
    meal_photos = _normalize_photo_urls(
        payload.get("meal_photos") if isinstance(payload, Mapping) else None
    )
    expenses_total = _normalize_number(
        payload.get("expenses_total") if isinstance(payload, Mapping) else None
    )
    expenses_total_value, expenses_count, expenses_top, expenses_remaining = (
        _normalize_expenses(payload if isinstance(payload, Mapping) else {})
    )
    location_summary = _normalize_text(
        payload.get("location_summary") if isinstance(payload, Mapping) else None
    )
    mood = _normalize_text(payload.get("mood") if isinstance(payload, Mapping) else None)
    weight = _normalize_number(payload.get("weight") if isinstance(payload, Mapping) else None)
    sleep_analysis_jp = _optional_text(
        payload.get("sleep_analysis_jp") if isinstance(payload, Mapping) else None
    )
    today_condition_forecast_jp = _optional_text(
        payload.get("today_condition_forecast_jp") if isinstance(payload, Mapping) else None
    )
    today_advice = _optional_text(
        payload.get("today_advice") if isinstance(payload, Mapping) else None
    )
    study_minutes = _safe_float(payload.get("study_minutes") if isinstance(payload, Mapping) else None)
    study_sessions = _safe_int(payload.get("study_sessions") if isinstance(payload, Mapping) else None)
    study_last_used_at_raw = _optional_text(
        payload.get("study_last_used_at") if isinstance(payload, Mapping) else None
    )
    study_last_used_at = _format_sleep_clock(study_last_used_at_raw) or study_last_used_at_raw
    f_risk_payload = payload.get("f_risk_alert_payload") if isinstance(payload, Mapping) else None
    f_risk_matched = False
    f_risk_alert = None
    f_risk_score = "—"
    f_risk_reason = None
    f_risk_patterns = None
    if isinstance(f_risk_payload, Mapping):
        f_risk_matched = bool(f_risk_payload.get("matched"))
        f_risk_alert = _optional_text(f_risk_payload.get("alert_text"))
        f_risk_score = _normalize_number(f_risk_payload.get("score"))
        f_risk_reason = _optional_text(f_risk_payload.get("reason"))
        matched_patterns_raw = f_risk_payload.get("matched_patterns")
        if isinstance(matched_patterns_raw, list):
            f_risk_patterns = " / ".join(
                str(item).strip() for item in matched_patterns_raw if str(item).strip()
            )
    weather_location = _optional_text(payload.get("weather_location") if isinstance(payload, Mapping) else None)
    weather_summary, weather_summary_source = _resolve_weather_summary(payload if isinstance(payload, Mapping) else {})
    weather_label = _resolve_weather_label(payload if isinstance(payload, Mapping) else {})
    weather_temp_max = _normalize_number(payload.get("weather_temp_max_c") if isinstance(payload, Mapping) else None)
    weather_temp_min = _normalize_number(payload.get("weather_temp_min_c") if isinstance(payload, Mapping) else None)
    weather_retrieved_at = _optional_text(payload.get("weather_retrieved_at") if isinstance(payload, Mapping) else None)
    sleep_start = _format_sleep_clock(
        payload.get("sleep_start") if isinstance(payload, Mapping) else None
    )
    sleep_end = _format_sleep_clock(payload.get("sleep_end") if isinstance(payload, Mapping) else None)
    sleep_duration = _format_sleep_duration(
        payload.get("sleep_duration_min") if isinstance(payload, Mapping) else None
    )
    sleep_score = _normalize_number(payload.get("sleep_score") if isinstance(payload, Mapping) else None)
    readiness_stars = _normalize_number(payload.get("readiness_stars") if isinstance(payload, Mapping) else None)
    readiness_hrv = _normalize_number(payload.get("readiness_hrv") if isinstance(payload, Mapping) else None)
    readiness_bpm = _normalize_number(payload.get("readiness_bpm") if isinstance(payload, Mapping) else None)
    baseline_hrv = _normalize_number(payload.get("baseline_hrv") if isinstance(payload, Mapping) else None)
    baseline_waking_bpm = _normalize_number(payload.get("baseline_waking_bpm") if isinstance(payload, Mapping) else None)
    sleep_heart_rate = _normalize_number(payload.get("sleep_heart_rate") if isinstance(payload, Mapping) else None)
    deep_duration = _format_sleep_duration(payload.get("deep_duration_min") if isinstance(payload, Mapping) else None)
    rem_duration = _format_sleep_duration(payload.get("rem_duration_min") if isinstance(payload, Mapping) else None)
    sleep_source = _optional_text(payload.get("sleep_source") if isinstance(payload, Mapping) else None)
    study_duration = _format_study_duration(study_minutes)
    mood_notes_url = str(payload.get("mood_notes_url") or "")
    today_advice_html = ""
    if today_advice:
        today_advice_html = (
            "<div style=\"margin-bottom:20px;padding:16px;border-radius:12px;background:#eff6ff;border:1px solid #bfdbfe;\">"
            "<div style=\"font-size:13px;font-weight:700;color:#1d4ed8;margin-bottom:8px;\">Today advice</div>"
            f"<div style=\"font-size:14px;line-height:1.8;color:#1f2937;white-space:pre-wrap;\">{html.escape(today_advice)}</div>"
            "</div>"
        )
    study_html = ""
    if study_duration:
        study_rows = [
            ("勉強時間", study_duration),
            ("セッション数", str(study_sessions) if study_sessions is not None else "—"),
            ("最終利用", study_last_used_at or "—"),
        ]
        study_html = (
            "<tr><td style=\"padding: 0 24px 16px 24px;\">"
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">"
            "<tr><td><h2 style=\"margin: 0 0 12px 0; font-size: 16px;\">司法試験 Study</h2>"
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">"
            + "".join(
                f"<tr><td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">{html.escape(label)}</td>"
                f"<td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{html.escape(value)}</td></tr>"
                for label, value in study_rows
            )
            + "</table></td></tr></table></td></tr>"
        )
    weather_html = ""
    if weather_summary:
        weather_html = (
            "<div style=\"margin-bottom:20px;padding:16px;border-radius:12px;background:#eefdf3;border:1px solid #bbf7d0;\">"
            "<div style=\"font-size:13px;font-weight:700;color:#166534;margin-bottom:8px;\">Weather</div>"
            f"<div style=\"font-size:14px;line-height:1.7;color:#1f2937;\">地点: {html.escape(weather_location or '—')} / 概要: {html.escape(weather_summary)} / 天気: {html.escape(weather_label)} / 最高: {html.escape(weather_temp_max)}℃ / 最低: {html.escape(weather_temp_min)}℃ / 取得時刻: {html.escape(weather_retrieved_at or '—')}</div>"
            "</div>"
        )
    elif weather_summary_source == "empty":
        weather_html = (
            "<div style=\"margin-bottom:20px;padding:16px;border-radius:12px;background:#eefdf3;border:1px solid #bbf7d0;\">"
            "<div style=\"font-size:13px;font-weight:700;color:#166534;margin-bottom:8px;\">Weather</div>"
            "<div style=\"font-size:14px;line-height:1.7;color:#1f2937;\">未取得</div>"
            "</div>"
        )
    f_risk_html = ""
    if f_risk_matched and f_risk_alert:
        detail_parts = []
        if f_risk_score != "—":
            detail_parts.append(f"score={f_risk_score}")
        if f_risk_patterns:
            detail_parts.append(f"patterns={f_risk_patterns}")
        if f_risk_reason:
            detail_parts.append(f"reason={f_risk_reason}")
        detail_line = " | ".join(detail_parts)
        detail_html = (
            f"<div style=\"font-size:12px;color:#7c2d12;margin-top:8px;\">{html.escape(detail_line)}</div>"
            if detail_line
            else ""
        )
        f_risk_html = (
            "<div style=\"margin-bottom:20px;padding:16px;border-radius:12px;background:#fff7ed;border:1px solid #fed7aa;\">"
            "<div style=\"font-size:13px;font-weight:700;color:#9a3412;margin-bottom:8px;\">F Risk Alert</div>"
            f"<div style=\"font-size:14px;line-height:1.8;color:#1f2937;white-space:pre-wrap;\">{html.escape(f_risk_alert)}</div>"
            f"{detail_html}"
            "</div>"
        )
    expense_f_alert_html = ""

    diary_html = html.escape(diary).replace("\n", "<br />")
    meal_summary_html = html.escape(meal_summary).replace("\n", "<br />")
    location_html = html.escape(location_summary).replace("\n", "<br />")
    sleep_lines = [
        ("Sleep Analysis JP", sleep_analysis_jp),
        ("Today Condition Forecast JP", today_condition_forecast_jp),
        ("就寝時間", sleep_start),
        ("起床時間", sleep_end),
        ("睡眠時間", sleep_duration),
        ("Sleep Score", None if sleep_score == "—" else sleep_score),
        ("Readiness Stars", None if readiness_stars == "—" else readiness_stars),
        ("Readiness HRV", None if readiness_hrv == "—" else readiness_hrv),
        ("Readiness BPM", None if readiness_bpm == "—" else readiness_bpm),
        ("Baseline HRV", None if baseline_hrv == "—" else baseline_hrv),
        ("Baseline Waking BPM", None if baseline_waking_bpm == "—" else baseline_waking_bpm),
        ("Sleep Heart Rate", None if sleep_heart_rate == "—" else sleep_heart_rate),
        ("Deep Duration", deep_duration),
        ("REM Duration", rem_duration),
        ("Sleep Source", sleep_source),
    ]
    visible_sleep_lines = [(label, value) for label, value in sleep_lines if value]
    sleep_condition_html = ""
    if visible_sleep_lines:
        sleep_condition_rows = []
        for label, value in visible_sleep_lines:
            rendered = html.escape(value).replace("\n", "<br />")
            sleep_condition_rows.append(
            "<tr>"
            f"<td style=\"padding: 6px 0; font-size: 13px; color: #6b7280; vertical-align: top;\">{html.escape(label)}</td>"
            f"<td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{rendered}</td>"
            "</tr>"
            )
        sleep_condition_html = (
            "<tr>"
            "<td style=\"padding: 0 24px 16px 24px;\">"
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" "
            "style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">"
            "<tr><td>"
            "<h2 style=\"margin: 0 0 12px 0; font-size: 16px;\">Sleep &amp; Condition</h2>"
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">"
            f"{''.join(sleep_condition_rows)}"
            "</table>"
            "</td></tr></table>"
            "</td>"
            "</tr>"
        )

    expenses_list_html = ""
    if expenses_top:
        rows = []
        for item in expenses_top:
            title = html.escape(str(item.get("title") or "Untitled"))
            amount = _format_yen(item.get("amount"))
            url = str(item.get("url") or "")
            if url:
                safe_url = html.escape(url, quote=True)
                link_html = f'<a href="{safe_url}">Open</a>'
            else:
                link_html = "Open"
            rows.append(
                "<li style=\"margin: 6px 0; font-size: 14px; color: #111827;\">"
                f"{title} — {amount} ({link_html})"
                "</li>"
            )
        if expenses_remaining > 0:
            rows.append(
                "<li style=\"margin: 6px 0; font-size: 13px; color: #6b7280;\">"
                f"…and {expenses_remaining} more"
                "</li>"
            )
        expenses_list_html = (
            "<ul style=\"margin: 8px 0 0 16px; padding: 0;\">"
            f"{''.join(rows)}"
            "</ul>"
        )
    else:
        expenses_list_html = (
            "<p style=\"margin: 8px 0 0 0; font-size: 14px; color: #9ca3af;\">—</p>"
        )

    done_rows = _render_task_rows(done_visible) + _render_more_row(done_more)
    drop_rows = _render_task_rows(drop_visible) + _render_more_row(drop_more)
    meal_photo_html = ""
    if meal_photos:
        images = []
        links = []
        for url in meal_photos:
            safe_url = html.escape(url, quote=True)
            if _is_renderable_image_url(url):
                images.append(
                    "<div style=\"margin: 0 8px 8px 0;\">"
                    f"<img src=\"{safe_url}\" alt=\"Meal photo\" "
                    "style=\"width: 160px; height: auto; border-radius: 8px; "
                    "border: 1px solid #e5e7eb; display: block;\" />"
                    "</div>"
                )
            else:
                links.append(f'<li style=\"margin:6px 0;\"><a href=\"{safe_url}\">{safe_url}</a></li>')
        blocks = []
        if images:
            blocks.append("<div style=\"display: flex; flex-wrap: wrap; margin-top: 8px;\">" + ''.join(images) + "</div>")
        if links:
            blocks.append("<ul style=\"margin: 8px 0 0 16px; padding: 0;\">" + ''.join(links) + "</ul>")
        meal_photo_html = ''.join(blocks)
    else:
        meal_photo_html = (
            "<p style=\"margin: 8px 0 0 0; font-size: 14px; color: #9ca3af;\">—</p>"
        )

    mood_notes_html = ""
    if mood_notes_url:
        safe_url = html.escape(mood_notes_url, quote=True)
        mood_notes_html = (
            "<tr>"
            "<td style=\"padding: 0 24px 24px 24px;\">"
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" "
            "style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">"
            "<tr><td>"
            "<h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">Mood / Notes</h2>"
            "<p style=\"margin: 0 0 12px 0; font-size: 14px; color: #6b7280;\">"
            "メールのリンクは確認ページのみ表示され、更新はPOSTで実行されます。"
            "</p>"
            "<a href=\"{safe_url}\" "
            "style=\"display:inline-block;padding:10px 16px;border-radius:8px;"
            "background:#111827;color:#ffffff;text-decoration:none;font-size:14px;\">"
            "Mood / Notes を入力</a>"
            "<p style=\"margin: 8px 0 0 0; font-size: 12px; color: #9ca3af;\">"
            f"{safe_url}</p>"
            "</td></tr></table>"
            "</td>"
            "</tr>"
        )

    return f"""\
<!DOCTYPE html>
<html lang=\"ja\">
  <head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Daily Log | {html.escape(target_date)}</title>
    <style>
      body, table, td, p, li {{
        font-family: \"Meiryo UI\", \"Meiryo\", \"Hiragino Kaku Gothic ProN\", \"Hiragino Sans\", -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
      }}
    </style>
  </head>
  <body style=\"margin: 0; padding: 0; background-color: #f6f7f9; font-family: 'Meiryo UI', 'Meiryo', 'Hiragino Kaku Gothic ProN', 'Hiragino Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111827;\">
    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background-color: #f6f7f9; padding: 24px 0;\">
      <tr>
        <td align=\"center\" style=\"padding: 0 12px;\">
          <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width: 640px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; overflow: hidden;\">
            <tr>
              <td style=\"padding: 24px 24px 16px 24px;\">
                <h1 style=\"margin: 0 0 8px 0; font-size: 22px; line-height: 1.3;\">Daily Log | {html.escape(target_date)}</h1>
                <p style=\"margin: 0; font-size: 13px; color: #6b7280;\">Run ID: {html.escape(run_id)}</p>
              </td>
            </tr>
            {today_advice_html}
            {f_risk_html}
            {expense_f_alert_html}
            {study_html}

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">Diary</h2>
                      <p style=\"margin: 0; font-size: 14px; color: #111827;\">{diary_html}</p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 12px 0; font-size: 16px;\">Summary</h2>
                      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">Expenses total</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{html.escape(expenses_total)}</td>
                        </tr>
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">Location summary</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{location_html}</td>
                        </tr>
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">Mood</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{html.escape(mood)}</td>
                        </tr>
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">Weight</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{html.escape(weight)}</td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            {sleep_condition_html}

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">Expenses (昨日の支出)</h2>
                      <p style=\"margin: 0; font-size: 14px; color: #111827;\"><strong>Total: {_format_yen(expenses_total_value)}</strong></p>
                      {expenses_list_html}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">🎉 昨日完了したこと（Done: {done_count_display}）</h2>
                      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">{done_rows}</table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">🧹 昨日手放したこと（Drop: {drop_count_display}）</h2>
                      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">{drop_rows}</table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 24px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">🍽️ Meal summary</h2>
                      <p style=\"margin: 0; font-size: 14px; color: #111827;\">{meal_summary_html}</p>
                      <p style=\"margin: 12px 0 0 0; font-size: 13px; color: #6b7280;\">Meal Photos</p>
                      {meal_photo_html}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            {mood_notes_html}
            {weather_html}
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def render_daily_log_text(payload: Mapping[str, object]) -> str:
    target_date = str(payload.get("target_date") or "")
    run_id = str(payload.get("run_id") or payload.get("mail_id") or "")
    summary_text = str(payload.get("summary_text") or "")

    done_items, drop_items = _parse_task_items(summary_text)
    done_count_display = _resolve_count(payload, "done_count", len(done_items))
    drop_count_display = _resolve_count(payload, "drop_count", len(drop_items))
    done_visible, done_more = _limit_items(done_items)
    drop_visible, drop_more = _limit_items(drop_items)

    def render_items(items: Iterable[TaskEntry], remaining: int) -> List[str]:
        lines = []
        if not items:
            return ["—"]
        else:
            for item in items:
                lines.append(f"- {item.title} (Priority: {item.priority})")
        if remaining > 0:
            lines.append(f"...and {remaining} more")
        return lines

    diary = _normalize_text(payload.get("diary") if isinstance(payload, Mapping) else None)
    meal_summary = _normalize_text(
        payload.get("meal_summary") if isinstance(payload, Mapping) else None
    )
    meal_photos = _normalize_photo_urls(
        payload.get("meal_photos") if isinstance(payload, Mapping) else None
    )
    expenses_total = _normalize_number(
        payload.get("expenses_total") if isinstance(payload, Mapping) else None
    )
    expenses_total_value, expenses_count, expenses_top, expenses_remaining = (
        _normalize_expenses(payload if isinstance(payload, Mapping) else {})
    )
    location_summary = _normalize_text(
        payload.get("location_summary") if isinstance(payload, Mapping) else None
    )
    mood = _normalize_text(payload.get("mood") if isinstance(payload, Mapping) else None)
    weight = _normalize_number(payload.get("weight") if isinstance(payload, Mapping) else None)
    sleep_analysis_jp = _optional_text(
        payload.get("sleep_analysis_jp") if isinstance(payload, Mapping) else None
    )
    today_condition_forecast_jp = _optional_text(
        payload.get("today_condition_forecast_jp") if isinstance(payload, Mapping) else None
    )
    today_advice = _optional_text(
        payload.get("today_advice") if isinstance(payload, Mapping) else None
    )
    study_minutes = _safe_float(payload.get("study_minutes") if isinstance(payload, Mapping) else None)
    study_sessions = _safe_int(payload.get("study_sessions") if isinstance(payload, Mapping) else None)
    study_last_used_at_raw = _optional_text(
        payload.get("study_last_used_at") if isinstance(payload, Mapping) else None
    )
    study_last_used_at = _format_sleep_clock(study_last_used_at_raw) or study_last_used_at_raw
    f_risk_payload = payload.get("f_risk_alert_payload") if isinstance(payload, Mapping) else None
    f_risk_matched = False
    f_risk_alert = None
    f_risk_score = "—"
    f_risk_reason = None
    f_risk_patterns = None
    if isinstance(f_risk_payload, Mapping):
        f_risk_matched = bool(f_risk_payload.get("matched"))
        f_risk_alert = _optional_text(f_risk_payload.get("alert_text"))
        f_risk_score = _normalize_number(f_risk_payload.get("score"))
        f_risk_reason = _optional_text(f_risk_payload.get("reason"))
        matched_patterns_raw = f_risk_payload.get("matched_patterns")
        if isinstance(matched_patterns_raw, list):
            f_risk_patterns = " / ".join(
                str(item).strip() for item in matched_patterns_raw if str(item).strip()
            )
    weather_location = _optional_text(payload.get("weather_location") if isinstance(payload, Mapping) else None)
    weather_summary, weather_summary_source = _resolve_weather_summary(payload if isinstance(payload, Mapping) else {})
    weather_label = _resolve_weather_label(payload if isinstance(payload, Mapping) else {})
    weather_temp_max = _normalize_number(payload.get("weather_temp_max_c") if isinstance(payload, Mapping) else None)
    weather_temp_min = _normalize_number(payload.get("weather_temp_min_c") if isinstance(payload, Mapping) else None)
    weather_retrieved_at = _optional_text(payload.get("weather_retrieved_at") if isinstance(payload, Mapping) else None)
    sleep_start = _format_sleep_clock(
        payload.get("sleep_start") if isinstance(payload, Mapping) else None
    )
    sleep_end = _format_sleep_clock(payload.get("sleep_end") if isinstance(payload, Mapping) else None)
    sleep_duration = _format_sleep_duration(
        payload.get("sleep_duration_min") if isinstance(payload, Mapping) else None
    )
    sleep_score = _normalize_number(payload.get("sleep_score") if isinstance(payload, Mapping) else None)
    readiness_stars = _normalize_number(payload.get("readiness_stars") if isinstance(payload, Mapping) else None)
    readiness_hrv = _normalize_number(payload.get("readiness_hrv") if isinstance(payload, Mapping) else None)
    readiness_bpm = _normalize_number(payload.get("readiness_bpm") if isinstance(payload, Mapping) else None)
    baseline_hrv = _normalize_number(payload.get("baseline_hrv") if isinstance(payload, Mapping) else None)
    baseline_waking_bpm = _normalize_number(payload.get("baseline_waking_bpm") if isinstance(payload, Mapping) else None)
    sleep_heart_rate = _normalize_number(payload.get("sleep_heart_rate") if isinstance(payload, Mapping) else None)
    deep_duration = _format_sleep_duration(payload.get("deep_duration_min") if isinstance(payload, Mapping) else None)
    rem_duration = _format_sleep_duration(payload.get("rem_duration_min") if isinstance(payload, Mapping) else None)
    sleep_source = _optional_text(payload.get("sleep_source") if isinstance(payload, Mapping) else None)
    study_duration = _format_study_duration(study_minutes)
    mood_notes_url = str(payload.get("mood_notes_url") or "")

    expenses_lines: List[str] = []
    if expenses_top:
        for item in expenses_top:
            title = item.get("title") or "Untitled"
            amount = _format_yen(item.get("amount"))
            url = item.get("url") or ""
            suffix = f" {url}" if url else ""
            expenses_lines.append(f"• {title} — {amount}{suffix}")
        if expenses_remaining > 0:
            expenses_lines.append(f"...and {expenses_remaining} more")
    else:
        expenses_lines.append("—")

    lines: List[str] = [
        f"Daily Log | {target_date}",
        f"Run ID: {run_id}",
    ]
    if today_advice:
        lines += ["", "Today advice", today_advice]
    if study_duration:
        lines += [
            "",
            "司法試験 Study",
            f"- 勉強時間: {study_duration}",
            f"- セッション数: {study_sessions if study_sessions is not None else '—'}",
            f"- 最終利用: {study_last_used_at or '—'}",
        ]
    if f_risk_matched and f_risk_alert:
        lines += ["", "F Risk Alert", f_risk_alert]
        if f_risk_score != "—":
            lines.append(f"- score: {f_risk_score}")
        if f_risk_patterns:
            lines.append(f"- matched patterns: {f_risk_patterns}")
        if f_risk_reason:
            lines.append(f"- reason: {f_risk_reason}")

    lines += [
        "",
        "Diary",
        diary or "—",
        "",
        "Summary",
        f"- Expenses total: {expenses_total}",
        f"- Location summary: {location_summary}",
        f"- Weight: {weight}",
        "",
    ]
    sleep_lines = [
        ("Sleep Analysis JP", sleep_analysis_jp),
        ("Today Condition Forecast JP", today_condition_forecast_jp),
        ("就寝時間", sleep_start),
        ("起床時間", sleep_end),
        ("睡眠時間", sleep_duration),
        ("Sleep Score", None if sleep_score == "—" else sleep_score),
        ("Readiness Stars", None if readiness_stars == "—" else readiness_stars),
        ("Readiness HRV", None if readiness_hrv == "—" else readiness_hrv),
        ("Readiness BPM", None if readiness_bpm == "—" else readiness_bpm),
        ("Baseline HRV", None if baseline_hrv == "—" else baseline_hrv),
        ("Baseline Waking BPM", None if baseline_waking_bpm == "—" else baseline_waking_bpm),
        ("Sleep Heart Rate", None if sleep_heart_rate == "—" else sleep_heart_rate),
        ("Deep Duration", deep_duration),
        ("REM Duration", rem_duration),
        ("Sleep Source", sleep_source),
    ]
    visible_sleep_lines = [f"- {label}: {value}" for label, value in sleep_lines if value]
    if visible_sleep_lines:
        lines += ["Sleep & Condition", *visible_sleep_lines, ""]
    lines += [
        "Expenses (昨日の支出)",
        f"Total: {_format_yen(expenses_total_value)}",
        *expenses_lines,
        "",
        f"🎉 昨日完了したこと（Done: {done_count_display}）",
        *render_items(done_visible, done_more),
        "",
        f"🧹 昨日手放したこと（Drop: {drop_count_display}）",
        *render_items(drop_visible, drop_more),
        "",
        "🍽️ Meal summary",
        f"- {meal_summary}",
        "Meal Photos",
        *([f"- {url}" for url in meal_photos] if meal_photos else ["- —"]),
    ]
    if mood_notes_url:
        lines += ["", "Mood / Notes", mood_notes_url]
    if weather_summary:
        lines += [
            "",
            "Weather",
            f"- 地点: {weather_location or '—'}",
            f"- 概要: {weather_summary}",
            f"- 天気: {weather_label}",
            f"- 最高/最低: {weather_temp_max}℃ / {weather_temp_min}℃",
            f"- 取得時刻: {weather_retrieved_at or '—'}",
        ]
    elif weather_summary_source == "empty":
        lines += ["", "Weather", "- 未取得"]
    return "\n".join(lines).strip() + "\n"
