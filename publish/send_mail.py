from __future__ import annotations

from dataclasses import dataclass
from typing import List

from delivery.email_sender import send_email as send_email_raw


@dataclass(frozen=True)
class MailConfig:
    mail_from: str
    mail_to: List[str]
    gmail_app_password: str


def send_mail(
    config: MailConfig, subject: str, plain_text: str, html_body: str
) -> None:
    send_email_raw(
        config.mail_from,
        config.mail_to,
        config.gmail_app_password,
        subject,
        plain_text,
        html_body,
    )
