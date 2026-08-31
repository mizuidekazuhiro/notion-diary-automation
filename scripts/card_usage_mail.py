"""Classify card usage notification mails before Notion registration."""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

from delivery.email_sender import send_email

ANA_PAY_LOG_MESSAGE = (
    "ANA Pay charge detected. Skip Notion registration and forward email."
)


@dataclass(frozen=True)
class CardUsageMail:
    subject: str
    body: str
    mail_from: str = ""


def _normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.upper()
    normalized = normalized.replace("－", "ー").replace("ｰ", "ー").replace("-", "ー")
    normalized = re.sub(r"[\s\u3000]+", "", normalized)
    return normalized


def _extract_labeled_value(body: str, labels: tuple[str, ...]) -> str:
    for line in (body or "").splitlines():
        normalized_line = unicodedata.normalize("NFKC", line)
        for label in labels:
            normalized_label = unicodedata.normalize("NFKC", label)
            match = re.match(
                rf"^\s*{re.escape(normalized_label)}\s*(?:[:：])?\s*(.+?)\s*$",
                normalized_line,
            )
            if match:
                return match.group(1)
    return ""


def extract_card_name(body: str) -> str:
    return _extract_labeled_value(body, ("カード名称", "カード名"))


def extract_merchant(body: str) -> str:
    return _extract_labeled_value(body, ("【ご利用先】", "ご利用先", "利用先"))


def is_ana_jcb_sfc_gold_card(card_name: str) -> bool:
    normalized = _normalize_for_match(card_name)
    return "ANAJCBSFCゴールドカード" in normalized


def is_ana_pay_merchant(merchant: str) -> bool:
    normalized = _normalize_for_match(merchant)
    return (
        normalized in {"ANAPAY", "エイエヌエーペイ"}
        or "ANAPAY" in normalized
        or "エイエヌエーペイ" in normalized
    )


def is_ana_pay_charge_notification(body: str) -> bool:
    return is_ana_jcb_sfc_gold_card(extract_card_name(body)) and is_ana_pay_merchant(
        extract_merchant(body)
    )


def _split_addresses(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def forward_ana_pay_charge_email(
    mail: CardUsageMail,
    *,
    forward_to: Optional[list[str]] = None,
    mail_from: Optional[str] = None,
    gmail_app_password: Optional[str] = None,
) -> None:
    recipients = forward_to or _split_addresses(
        os.getenv("ANA_PAY_FORWARD_TO") or os.getenv("MAIL_TO", "")
    )
    sender = mail_from or os.getenv("MAIL_FROM", "")
    password = gmail_app_password or os.getenv("GMAIL_APP_PASSWORD", "")
    if not recipients:
        raise ValueError(
            "missing ANA Pay forward destination: set ANA_PAY_FORWARD_TO or MAIL_TO"
        )
    if not sender or not password:
        raise ValueError(
            "missing mail credentials: set MAIL_FROM and GMAIL_APP_PASSWORD"
        )

    subject = (
        mail.subject if mail.subject.startswith("Fwd:") else f"Fwd: {mail.subject}"
    )
    original_from = f"From: {mail.mail_from}\n" if mail.mail_from else ""
    body = f"Forwarded ANA Pay charge notification.\n\n--- Original Message ---\n{original_from}{mail.body}"
    send_email(sender, recipients, password, subject, body, body.replace("\n", "<br>"))


def handle_card_usage_mail(
    mail: CardUsageMail,
    *,
    register_to_notion: Callable[[CardUsageMail], object],
    forward_email: Callable[[CardUsageMail], object] = forward_ana_pay_charge_email,
) -> object | None:
    if is_ana_pay_charge_notification(mail.body):
        logging.info(ANA_PAY_LOG_MESSAGE)
        forward_email(mail)
        return None
    return register_to_notion(mail)
