from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from delivery.email_sender import InlineImage, send_email as send_email_raw


@dataclass(frozen=True)
class MailConfig:
    mail_from: str
    mail_to: List[str]
    gmail_app_password: str
    mail_cc: List[str] = field(default_factory=list)
    mail_bcc: List[str] = field(default_factory=list)


def send_mail(
    config: MailConfig, subject: str, plain_text: str, html_body: str, *, inline_images: Optional[List[InlineImage]] = None
) -> None:
    send_email_raw(
        config.mail_from,
        config.mail_to,
        config.gmail_app_password,
        subject,
        plain_text,
        html_body,
        mail_cc=config.mail_cc,
        mail_bcc=config.mail_bcc,
        inline_images=inline_images,
    )


__all__ = ["MailConfig", "InlineImage", "send_mail"]
