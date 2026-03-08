from __future__ import annotations

from dataclasses import dataclass
import html


@dataclass(frozen=True)
class DiaryNotificationMail:
    subject: str
    plain_text: str
    html_body: str


def build_diary_notification_text(*, target_date: str, diary: str, page_url: str) -> str:
    diary_text = diary.strip()
    return "\n".join(
        [
            f"{target_date} のDiaryを生成しました。",
            "",
            "Diary:",
            diary_text,
            "",
            "Daily Log:",
            page_url,
            "",
        ]
    )


def build_diary_notification_html(*, target_date: str, diary: str, page_url: str) -> str:
    safe_diary = html.escape(diary.strip()).replace("\n", "<br />")
    safe_url = html.escape(page_url, quote=True)
    return f"""<!DOCTYPE html>
<html lang=\"ja\">
  <head>
    <meta charset=\"UTF-8\" />
    <title>Diary generated</title>
  </head>
  <body style=\"font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; color: #111827;\">
    <div style=\"max-width: 640px; margin: 0 auto; padding: 24px 16px;\">
      <h1 style=\"margin: 0 0 12px 0; font-size: 20px;\">Diaryを生成しました（{html.escape(target_date)}）</h1>
      <div style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; background: #ffffff;\">
        <p style=\"margin: 0; font-size: 14px; line-height: 1.7;\">{safe_diary}</p>
      </div>
      <p style=\"margin: 16px 0 0 0; font-size: 14px;\">
        <a href=\"{safe_url}\">Daily Log を開く</a>
      </p>
    </div>
  </body>
</html>
"""


def render_diary_notification_mail(*, target_date: str, diary: str, page_url: str) -> DiaryNotificationMail:
    subject = f"【Daily Log】Diaryを生成しました（{target_date}）"
    plain_text = build_diary_notification_text(
        target_date=target_date,
        diary=diary,
        page_url=page_url,
    )
    html_body = build_diary_notification_html(
        target_date=target_date,
        diary=diary,
        page_url=page_url,
    )
    return DiaryNotificationMail(subject=subject, plain_text=plain_text, html_body=html_body)
