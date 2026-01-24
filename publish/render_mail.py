from __future__ import annotations

from dataclasses import dataclass

from publish.email_templates import render_daily_log_html, render_daily_log_text
from publish.read_daily_log import DailyLogSummary


@dataclass(frozen=True)
class MailContent:
    subject: str
    plain_text: str
    html_body: str


def render_mail(summary: DailyLogSummary) -> MailContent:
    subject = f"Daily Log | {summary.target_date}"
    payload = {
        "target_date": summary.target_date,
        "run_id": summary.mail_id,
        "summary_text": summary.summary_text,
        "diary": summary.diary,
        "expenses_total": summary.expenses_total,
        "location_summary": summary.location_summary,
        "mood": summary.mood,
        "weight": summary.weight,
    }
    plain_text = render_daily_log_text(payload)
    html_body = render_daily_log_html(payload)

    return MailContent(subject=subject, plain_text=plain_text, html_body=html_body)
