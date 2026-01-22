from __future__ import annotations

from daily_job import build_email_message


def main() -> None:
    message = build_email_message(
        mail_from="from@example.com",
        mail_to=["to@example.com"],
        subject="Test Subject",
        plain_text="Plain text body\n",
        html_body="<html><body><p>HTML body</p></body></html>",
    )
    raw = message.as_string()
    assert "multipart/alternative" in raw
    assert "Content-Type: text/plain" in raw
    assert "Content-Type: text/html" in raw
    assert "charset=\"utf-8\"" in raw.lower() or "charset=utf-8" in raw.lower()
    print("OK: multipart/alternative with text/plain and text/html")


if __name__ == "__main__":
    main()
