import logging
import smtplib
from dataclasses import dataclass
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import List, Optional


@dataclass(frozen=True)
class InlineImage:
    cid: str
    filename: str
    data: bytes
    mime_type: str = "image/png"


def build_email_message(
    mail_from: str,
    mail_to: List[str],
    subject: str,
    plain_text: str,
    html_body: str,
    *,
    mail_cc: Optional[List[str]] = None,
    mail_bcc: Optional[List[str]] = None,
    inline_images: Optional[List[InlineImage]] = None,
) -> MIMEMultipart:
    recipients_to = list(mail_to)
    recipients_cc = list(mail_cc or [])
    _ = list(mail_bcc or [])

    if inline_images:
        message = MIMEMultipart("related")
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(plain_text, "plain", "utf-8"))
        alternative.attach(MIMEText(html_body, "html", "utf-8"))
        message.attach(alternative)
        for image in inline_images:
            maintype, subtype = image.mime_type.split("/", 1)
            part = MIMEBase(maintype, subtype)
            part.set_payload(image.data)
            encoders.encode_base64(part)
            part.add_header("Content-ID", f"<{image.cid}>")
            part.add_header("Content-Disposition", "inline", filename=image.filename)
            message.attach(part)
    else:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(plain_text, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))

    message["Subject"] = subject
    message["From"] = mail_from
    message["To"] = ", ".join(recipients_to)
    if recipients_cc:
        message["Cc"] = ", ".join(recipients_cc)
    return message


def send_email(
    mail_from: str,
    mail_to: List[str],
    gmail_app_password: str,
    subject: str,
    plain_text: str,
    html_body: str,
    *,
    mail_cc: Optional[List[str]] = None,
    mail_bcc: Optional[List[str]] = None,
    inline_images: Optional[List[InlineImage]] = None,
) -> None:
    logger = logging.getLogger(__name__)
    recipients = list(mail_to) + list(mail_cc or []) + list(mail_bcc or [])
    message = build_email_message(
        mail_from,
        mail_to,
        subject,
        plain_text,
        html_body,
        mail_cc=mail_cc,
        mail_bcc=mail_bcc,
        inline_images=inline_images,
    )
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(mail_from, gmail_app_password)
            server.sendmail(mail_from, recipients, message.as_string())
    except Exception:
        logger.exception(
            "Failed to send email via SMTP. The job will continue without stopping."
        )
