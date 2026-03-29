from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import os
import time
from typing import Any, Optional

from publish.email_templates import render_daily_log_html, render_daily_log_text
from publish.read_daily_log import DailyLogSummary


@dataclass(frozen=True)
class MailContent:
    subject: str
    plain_text: str
    html_body: str


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _sign_payload(payload: str, secret: str) -> str:
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256)
    return f"{_base64url_encode(payload.encode('utf-8'))}.{_base64url_encode(signature.digest())}"


def _build_mood_notes_url(target_date: str) -> str:
    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not public_base_url:
        raise RuntimeError("Missing env var: PUBLIC_BASE_URL")
    secret = os.getenv("MAIL_LINK_SECRET", "").strip()
    if not secret:
        raise RuntimeError("Missing env var: MAIL_LINK_SECRET")

    exp = int(time.time()) + 60 * 60 * 48
    payload = f"date={target_date}&exp={exp}"
    token = _sign_payload(payload, secret)
    base_url = public_base_url.rstrip("/")
    return f"{base_url}/confirm/mood-notes?date={target_date}&token={token}"


def render_mail(
    summary: DailyLogSummary,
    *,
    expense_f_alert: Optional[dict[str, Any]] = None,
    f_risk_alert: Optional[dict[str, Any]] = None,
) -> MailContent:
    subject = f"Daily Log | {summary.target_date}"
    mood_notes_url = _build_mood_notes_url(summary.target_date)
    payload = {
        "target_date": summary.target_date,
        "run_id": summary.mail_id,
        "summary_text": summary.summary_text,
        "done_count": summary.done_count,
        "drop_count": summary.drop_count,
        "diary": summary.diary,
        "meal_summary": summary.meal_summary,
        "meal_photos": summary.meal_photos,
        "expenses_total": summary.expenses_total,
        "expenses": {
            "total": summary.expenses.total,
            "count": summary.expenses.count,
            "top": [
                {"title": item.title, "amount": item.amount, "url": item.url}
                for item in summary.expenses.top
            ],
            "remaining": summary.expenses.remaining,
        },
        "location_summary": summary.location_summary,
        "mood": summary.mood,
        "weight": summary.weight,
        "sleep_analysis_jp": summary.sleep_analysis_jp,
        "today_condition_forecast_jp": summary.today_condition_forecast_jp,
        "today_advice": summary.today_advice,
        "f_risk_alert_payload": f_risk_alert or {},
        "sleep_start": summary.sleep_start,
        "sleep_end": summary.sleep_end,
        "sleep_duration_min": summary.resolved_sleep_duration_min,
        "resolved_sleep_duration_min": summary.resolved_sleep_duration_min,
        "resolved_sleep_duration_hours": summary.resolved_sleep_duration_hours,
        "resolved_sleep_duration_text": summary.resolved_sleep_duration_text,
        "sleep_duration_source": summary.sleep_duration_source,
        "sleep_score": summary.sleep_score,
        "readiness_stars": summary.readiness_stars,
        "readiness_hrv": summary.readiness_hrv,
        "readiness_bpm": summary.readiness_bpm,
        "baseline_hrv": summary.baseline_hrv,
        "baseline_waking_bpm": summary.baseline_waking_bpm,
        "sleep_heart_rate": summary.sleep_heart_rate,
        "deep_duration_min": summary.deep_duration_min,
        "rem_duration_min": summary.rem_duration_min,
        "sleep_source": summary.sleep_source,
        "weather_location": summary.weather_location,
        "weather": summary.weather_summary,
        "weather_summary": summary.weather_summary,
        "weather_temp_max_c": summary.weather_temp_max_c,
        "weather_temp_min_c": summary.weather_temp_min_c,
        "weather_precip_probability_max": summary.weather_precip_probability_max,
        "weather_code": summary.weather_code,
        "weather_retrieved_at": summary.weather_retrieved_at,
        "expense_f_alert": expense_f_alert or {},
        "mood_notes_url": "",
    }
    plain_text = render_daily_log_text(payload)
    if mood_notes_url:
        mood_notes_text = f"""
Mood / Notes を入力:
{mood_notes_url}
"""
        plain_text = f"{plain_text.rstrip()}\n\n{mood_notes_text.strip()}\n"

    html_body = render_daily_log_html(payload)
    if mood_notes_url:
        mood_notes_html = f"""
<div style="margin-top:16px">
  <a href="{mood_notes_url}"
     style="
       display:inline-block;
       padding:10px 14px;
       background:#4f46e5;
       color:#ffffff;
       text-decoration:none;
       border-radius:6px;
       font-weight:600;
     ">
     Mood / Notes を入力
  </a>
</div>
"""
        body_marker = "</body>"
        marker_index = html_body.rfind(body_marker)
        if marker_index != -1:
            html_body = f"{html_body[:marker_index]}{mood_notes_html}{html_body[marker_index:]}"
        else:
            html_body = f"{html_body}{mood_notes_html}"

    return MailContent(subject=subject, plain_text=plain_text, html_body=html_body)
