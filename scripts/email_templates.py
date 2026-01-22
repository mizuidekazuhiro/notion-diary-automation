from __future__ import annotations

import html
from typing import Iterable, List


def _escape_items(items: Iterable[str]) -> List[str]:
    return [html.escape(item) for item in items]


def _render_list_html(items: Iterable[str]) -> str:
    escaped = _escape_items(items)
    if not escaped:
        return '<li style="margin: 0 0 6px 0;">None</li>'
    return "".join(
        f'<li style="margin: 0 0 6px 0;">{item}</li>' for item in escaped
    )


def build_email_html(
    *,
    date_str: str,
    run_id: str,
    progress_line: str,
    done_items: Iterable[str],
    drop_items: Iterable[str],
    task_items: Iterable[str],
    inbox_items: Iterable[str],
    activity_summary: str,
) -> str:
    done_list = list(done_items)
    drop_list = list(drop_items)
    task_list = list(task_items)
    inbox_list = list(inbox_items)
    activity_text = html.escape(activity_summary)
    return f"""\
<!DOCTYPE html>
<html lang="ja">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(date_str)} Daily Summary</title>
  </head>
  <body style="margin: 0; padding: 0; background-color: #f5f7fb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #222;">
    <div style="max-width: 720px; margin: 0 auto; padding: 24px 16px;">
      <div style="padding: 12px 0 20px 0;">
        <h2 style="margin: 0 0 8px 0; font-size: 22px; line-height: 1.3;">{html.escape(date_str)} Daily Summary</h2>
        <p style="margin: 0; font-size: 13px; color: #6b7280;">Run ID: {html.escape(run_id)}</p>
      </div>

      <div style="background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin: 0 0 16px 0;">
        <h3 style="margin: 0 0 12px 0; font-size: 16px;">🎉 昨日完了したこと（Done: {len(done_list)}）</h3>
        <ul style="padding-left: 20px; margin: 0;">
          {_render_list_html(done_list)}
        </ul>
      </div>

      <div style="background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin: 0 0 16px 0;">
        <h3 style="margin: 0 0 12px 0; font-size: 16px;">🧹 昨日手放したこと（Drop: {len(drop_list)}）</h3>
        <ul style="padding-left: 20px; margin: 0;">
          {_render_list_html(drop_list)}
        </ul>
      </div>

      <div style="background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin: 0 0 16px 0;">
        <p style="margin: 0 0 12px 0; font-weight: 600;">{html.escape(progress_line)}</p>
        <h3 style="margin: 0 0 12px 0; font-size: 16px;">Tasks (Status: Do)</h3>
        <ul style="padding-left: 20px; margin: 0 0 12px 0;">
          {_render_list_html(task_list)}
        </ul>
        <h3 style="margin: 0 0 12px 0; font-size: 16px;">Inbox</h3>
        <ul style="padding-left: 20px; margin: 0;">
          {_render_list_html(inbox_list)}
        </ul>
      </div>

      <div style="background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin: 0;">
        <h3 style="margin: 0 0 12px 0; font-size: 16px;">Activity Summary</h3>
        <div style="white-space: pre-wrap; font-size: 14px; line-height: 1.6;">{activity_text}</div>
      </div>
    </div>
  </body>
</html>
"""


def build_email_text(
    *,
    date_str: str,
    run_id: str,
    progress_line: str,
    done_items: Iterable[str],
    drop_items: Iterable[str],
    task_items: Iterable[str],
    inbox_items: Iterable[str],
    activity_summary: str,
) -> str:
    done_list = list(done_items)
    drop_list = list(drop_items)
    task_list = list(task_items)
    inbox_list = list(inbox_items)

    def render_list(items: Iterable[str]) -> str:
        items_list = list(items)
        if not items_list:
            return "- None"
        return "\n".join(f"- {item}" for item in items_list)

    sections = [
        f"{date_str} Daily Summary",
        f"Run ID: {run_id}",
        "",
        f"🎉 昨日完了したこと（Done: {len(done_list)}）",
        render_list(done_list),
        "",
        f"🧹 昨日手放したこと（Drop: {len(drop_list)}）",
        render_list(drop_list),
        "",
        progress_line,
        "",
        "Tasks (Status: Do)",
        render_list(task_list),
        "",
        "Inbox",
        render_list(inbox_list),
        "",
        "Activity Summary",
        activity_summary,
    ]
    return "\n".join(sections).strip() + "\n"
