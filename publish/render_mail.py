from __future__ import annotations

from dataclasses import dataclass

from publish.read_daily_log import DailyLogSummary


@dataclass(frozen=True)
class MailContent:
    subject: str
    plain_text: str
    html_body: str


def render_mail(summary: DailyLogSummary) -> MailContent:
    subject = f"Daily Log｜{summary.target_date}"
    plain_text = summary.summary_text or ""
    html_body = summary.summary_html or ""

    if not plain_text and html_body:
        plain_text = html_body
    if not html_body and plain_text:
        html_body = plain_text

    return MailContent(subject=subject, plain_text=plain_text, html_body=html_body)
