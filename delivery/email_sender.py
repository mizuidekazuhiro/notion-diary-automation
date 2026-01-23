import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List


def build_email_message(
    mail_from: str,
    mail_to: List[str],
    subject: str,
    plain_text: str,
    html_body: str,
) -> MIMEMultipart:
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = mail_from
    message["To"] = ", ".join(mail_to)
    message.attach(MIMEText(plain_text, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))
    return message


def send_email(
    mail_from: str,
    mail_to: List[str],
    gmail_app_password: str,
    subject: str,
    plain_text: str,
    html_body: str,
) -> None:
    logger = logging.getLogger(__name__)
    message = build_email_message(
        mail_from, mail_to, subject, plain_text, html_body
    )
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(mail_from, gmail_app_password)
            server.sendmail(mail_from, mail_to, message.as_string())
    except Exception:
        logger.exception(
            "Failed to send email via SMTP. The job will continue without stopping."
        )
