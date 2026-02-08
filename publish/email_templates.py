from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Tuple

MAX_TASK_ITEMS = 30


@dataclass(frozen=True)
class TaskEntry:
    title: str
    priority: str


def _normalize_text(value: Optional[str]) -> str:
    if value is None:
        return "—"
    stripped = value.strip()
    return stripped if stripped else "—"


def _normalize_number(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "—"
    return f"{value:g}"


def _format_yen(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "—"
    return f"¥{value:g}"


def _normalize_photo_urls(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    urls: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        url = item.strip()
        if url:
            urls.append(url)
    return urls


def _normalize_expenses(payload: Mapping[str, object]) -> Tuple[float, int, List[dict], int]:
    expenses_payload = payload.get("expenses")
    total = 0.0
    count = 0
    remaining = 0
    top: List[dict] = []

    if isinstance(expenses_payload, Mapping):
        total = float(expenses_payload.get("total") or 0)
        count = int(expenses_payload.get("count") or 0)
        remaining = int(expenses_payload.get("remaining") or 0)
        top_payload = expenses_payload.get("top", [])
        if isinstance(top_payload, list):
            for item in top_payload:
                if not isinstance(item, Mapping):
                    continue
                title = str(item.get("title") or "Untitled")
                amount = float(item.get("amount") or 0)
                url = str(item.get("url") or "")
                top.append({"title": title, "amount": amount, "url": url})
    else:
        total = float(payload.get("expenses_total") or 0)

    return total, count, top, remaining


def _parse_task_items(summary_text: str) -> Tuple[List[TaskEntry], List[TaskEntry]]:
    done_items: List[TaskEntry] = []
    drop_items: List[TaskEntry] = []
    current: Optional[str] = None
    if not summary_text:
        return done_items, drop_items

    priority_pattern = re.compile(
        r"^(?P<title>.*?)(?:\s*\(Priority:\s*(?P<priority>[^)]+)\))?$"
    )

    for raw_line in summary_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("🎉"):
            current = "done"
            continue
        if line.startswith("🧹"):
            current = "drop"
            continue
        if not line.startswith("-"):
            continue

        if current not in {"done", "drop"}:
            continue

        item_text = line[1:].strip()
        if not item_text:
            continue
        match = priority_pattern.match(item_text)
        if not match:
            title, priority = item_text, "-"
        else:
            title = (match.group("title") or "").strip()
            priority = (match.group("priority") or "-").strip()
        entry = TaskEntry(title=title or "(No title)", priority=priority or "-")
        if current == "done":
            done_items.append(entry)
        else:
            drop_items.append(entry)
    return done_items, drop_items


def _limit_items(items: List[TaskEntry]) -> Tuple[List[TaskEntry], int]:
    if len(items) <= MAX_TASK_ITEMS:
        return items, 0
    return items[:MAX_TASK_ITEMS], len(items) - MAX_TASK_ITEMS


def _render_priority_badge(priority: str) -> str:
    normalized = priority.strip().lower()
    color_map = {
        "high": ("#fee2e2", "#991b1b"),
        "mid": ("#fef3c7", "#92400e"),
        "medium": ("#fef3c7", "#92400e"),
        "low": ("#d1fae5", "#065f46"),
        "-": ("#e5e7eb", "#374151"),
        "": ("#e5e7eb", "#374151"),
    }
    background, text = color_map.get(normalized, ("#e5e7eb", "#374151"))
    label = html.escape(priority or "-")
    return (
        f"<span style=\"display: inline-block; padding: 2px 8px; "
        f"border-radius: 999px; font-size: 12px; background: {background}; "
        f"color: {text}; font-weight: 600; white-space: nowrap;\">{label}</span>"
    )


def _render_task_rows(items: List[TaskEntry]) -> str:
    if not items:
        return (
            "<tr>"
            "<td style=\"padding: 8px 0; color: #9ca3af; font-size: 14px;\">—</td>"
            "<td style=\"padding: 8px 0;\"></td>"
            "</tr>"
        )

    rows = []
    for item in items:
        title = html.escape(item.title)
        badge = _render_priority_badge(item.priority)
        rows.append(
            "<tr>"
            f"<td style=\"padding: 8px 0; font-size: 14px; color: #111827;\">{title}</td>"
            f"<td align=\"right\" style=\"padding: 8px 0;\">{badge}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_more_row(remaining: int) -> str:
    if remaining <= 0:
        return ""
    return (
        "<tr>"
        f"<td colspan=\"2\" style=\"padding: 8px 0; font-size: 13px; color: #6b7280;\">"
        f"...and {remaining} more"
        "</td>"
        "</tr>"
    )


def render_daily_log_html(payload: Mapping[str, object]) -> str:
    target_date = str(payload.get("target_date") or "")
    run_id = str(payload.get("run_id") or payload.get("mail_id") or "")
    summary_text = str(payload.get("summary_text") or "")

    done_items, drop_items = _parse_task_items(summary_text)
    done_visible, done_more = _limit_items(done_items)
    drop_visible, drop_more = _limit_items(drop_items)

    diary = _normalize_text(payload.get("diary") if isinstance(payload, Mapping) else None)
    meal_summary = _normalize_text(
        payload.get("meal_summary") if isinstance(payload, Mapping) else None
    )
    meal_photos = _normalize_photo_urls(
        payload.get("meal_photos") if isinstance(payload, Mapping) else None
    )
    expenses_total = _normalize_number(
        payload.get("expenses_total") if isinstance(payload, Mapping) else None
    )
    expenses_total_value, expenses_count, expenses_top, expenses_remaining = (
        _normalize_expenses(payload if isinstance(payload, Mapping) else {})
    )
    location_summary = _normalize_text(
        payload.get("location_summary") if isinstance(payload, Mapping) else None
    )
    mood = _normalize_text(payload.get("mood") if isinstance(payload, Mapping) else None)
    weight = _normalize_number(payload.get("weight") if isinstance(payload, Mapping) else None)
    mood_notes_url = str(payload.get("mood_notes_url") or "")

    diary_html = html.escape(diary).replace("\n", "<br />")
    meal_summary_html = html.escape(meal_summary).replace("\n", "<br />")
    location_html = html.escape(location_summary).replace("\n", "<br />")

    expenses_list_html = ""
    if expenses_top:
        rows = []
        for item in expenses_top:
            title = html.escape(str(item.get("title") or "Untitled"))
            amount = _format_yen(item.get("amount"))
            url = str(item.get("url") or "")
            if url:
                safe_url = html.escape(url, quote=True)
                link_html = f'<a href="{safe_url}">Open</a>'
            else:
                link_html = "Open"
            rows.append(
                "<li style=\"margin: 6px 0; font-size: 14px; color: #111827;\">"
                f"{title} — {amount} ({link_html})"
                "</li>"
            )
        if expenses_remaining > 0:
            rows.append(
                "<li style=\"margin: 6px 0; font-size: 13px; color: #6b7280;\">"
                f"…and {expenses_remaining} more"
                "</li>"
            )
        expenses_list_html = (
            "<ul style=\"margin: 8px 0 0 16px; padding: 0;\">"
            f"{''.join(rows)}"
            "</ul>"
        )
    else:
        expenses_list_html = (
            "<p style=\"margin: 8px 0 0 0; font-size: 14px; color: #9ca3af;\">—</p>"
        )

    done_rows = _render_task_rows(done_visible) + _render_more_row(done_more)
    drop_rows = _render_task_rows(drop_visible) + _render_more_row(drop_more)
    meal_photo_html = ""
    if meal_photos:
        images = []
        for url in meal_photos:
            safe_url = html.escape(url, quote=True)
            images.append(
                "<div style=\"margin: 0 8px 8px 0;\">"
                f"<img src=\"{safe_url}\" alt=\"Meal photo\" "
                "style=\"width: 160px; height: auto; border-radius: 8px; "
                "border: 1px solid #e5e7eb; display: block;\" />"
                "</div>"
            )
        meal_photo_html = (
            "<div style=\"display: flex; flex-wrap: wrap; margin-top: 8px;\">"
            f"{''.join(images)}"
            "</div>"
        )
    else:
        meal_photo_html = (
            "<p style=\"margin: 8px 0 0 0; font-size: 14px; color: #9ca3af;\">—</p>"
        )

    mood_notes_html = ""
    if mood_notes_url:
        safe_url = html.escape(mood_notes_url, quote=True)
        mood_notes_html = (
            "<tr>"
            "<td style=\"padding: 0 24px 24px 24px;\">"
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" "
            "style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">"
            "<tr><td>"
            "<h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">Mood / Notes</h2>"
            "<p style=\"margin: 0 0 12px 0; font-size: 14px; color: #6b7280;\">"
            "メールのリンクは確認ページのみ表示され、更新はPOSTで実行されます。"
            "</p>"
            "<a href=\"{safe_url}\" "
            "style=\"display:inline-block;padding:10px 16px;border-radius:8px;"
            "background:#111827;color:#ffffff;text-decoration:none;font-size:14px;\">"
            "Mood / Notes を入力</a>"
            "<p style=\"margin: 8px 0 0 0; font-size: 12px; color: #9ca3af;\">"
            "{safe_url}</p>"
            "</td></tr></table>"
            "</td>"
            "</tr>"
        )

    return f"""\
<!DOCTYPE html>
<html lang=\"ja\">
  <head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Daily Log | {html.escape(target_date)}</title>
    <style>
      body, table, td, p, li {{
        font-family: \"Meiryo UI\", \"Meiryo\", \"Hiragino Kaku Gothic ProN\", \"Hiragino Sans\", -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
      }}
    </style>
  </head>
  <body style=\"margin: 0; padding: 0; background-color: #f6f7f9; font-family: 'Meiryo UI', 'Meiryo', 'Hiragino Kaku Gothic ProN', 'Hiragino Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111827;\">
    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background-color: #f6f7f9; padding: 24px 0;\">
      <tr>
        <td align=\"center\" style=\"padding: 0 12px;\">
          <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width: 640px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; overflow: hidden;\">
            <tr>
              <td style=\"padding: 24px 24px 16px 24px;\">
                <h1 style=\"margin: 0 0 8px 0; font-size: 22px; line-height: 1.3;\">Daily Log | {html.escape(target_date)}</h1>
                <p style=\"margin: 0; font-size: 13px; color: #6b7280;\">Run ID: {html.escape(run_id)}</p>
              </td>
            </tr>

            <!-- Reorder sections for daily log readability; font stack set for consistent mail rendering. -->
            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">Diary</h2>
                      <p style=\"margin: 0; font-size: 14px; color: #111827;\">{diary_html}</p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 12px 0; font-size: 16px;\">Summary</h2>
                      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">Expenses total</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{html.escape(expenses_total)}</td>
                        </tr>
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">Location summary</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{location_html}</td>
                        </tr>
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">Mood</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{html.escape(mood)}</td>
                        </tr>
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280;\">Weight</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{html.escape(weight)}</td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">Expenses (昨日の支出)</h2>
                      <p style=\"margin: 0; font-size: 14px; color: #111827;\"><strong>Total: {_format_yen(expenses_total_value)}</strong></p>
                      {expenses_list_html}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">🎉 昨日完了したこと（Done: {len(done_items)}）</h2>
                      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">{done_rows}</table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 16px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">🧹 昨日手放したこと（Drop: {len(drop_items)}）</h2>
                      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">{drop_rows}</table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <tr>
              <td style=\"padding: 0 24px 24px 24px;\">
                <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;\">
                  <tr>
                    <td>
                      <h2 style=\"margin: 0 0 8px 0; font-size: 16px;\">🍽️ Meal summary</h2>
                      <p style=\"margin: 0; font-size: 14px; color: #111827;\">{meal_summary_html}</p>
                      <p style=\"margin: 12px 0 0 0; font-size: 13px; color: #6b7280;\">Meal Photos</p>
                      {meal_photo_html}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            {mood_notes_html}
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def render_daily_log_text(payload: Mapping[str, object]) -> str:
    target_date = str(payload.get("target_date") or "")
    run_id = str(payload.get("run_id") or payload.get("mail_id") or "")
    summary_text = str(payload.get("summary_text") or "")

    done_items, drop_items = _parse_task_items(summary_text)
    done_visible, done_more = _limit_items(done_items)
    drop_visible, drop_more = _limit_items(drop_items)

    def render_items(items: Iterable[TaskEntry], remaining: int) -> List[str]:
        lines = []
        if not items:
            lines.append("- —")
        else:
            for item in items:
                lines.append(f"- {item.title} (Priority: {item.priority})")
        if remaining > 0:
            lines.append(f"...and {remaining} more")
        return lines

    diary = _normalize_text(payload.get("diary") if isinstance(payload, Mapping) else None)
    meal_summary = _normalize_text(
        payload.get("meal_summary") if isinstance(payload, Mapping) else None
    )
    meal_photos = _normalize_photo_urls(
        payload.get("meal_photos") if isinstance(payload, Mapping) else None
    )
    expenses_total = _normalize_number(
        payload.get("expenses_total") if isinstance(payload, Mapping) else None
    )
    expenses_total_value, expenses_count, expenses_top, expenses_remaining = (
        _normalize_expenses(payload if isinstance(payload, Mapping) else {})
    )
    location_summary = _normalize_text(
        payload.get("location_summary") if isinstance(payload, Mapping) else None
    )
    mood = _normalize_text(payload.get("mood") if isinstance(payload, Mapping) else None)
    weight = _normalize_number(payload.get("weight") if isinstance(payload, Mapping) else None)
    mood_notes_url = str(payload.get("mood_notes_url") or "")

    expenses_lines: List[str] = []
    if expenses_top:
        for item in expenses_top:
            title = item.get("title") or "Untitled"
            amount = _format_yen(item.get("amount"))
            url = item.get("url") or ""
            suffix = f" {url}" if url else ""
            expenses_lines.append(f"• {title} — {amount}{suffix}")
        if expenses_remaining > 0:
            expenses_lines.append(f"...and {expenses_remaining} more")
    else:
        expenses_lines.append("—")

    lines = [
        f"Daily Log | {target_date}",
        f"Run ID: {run_id}",
        "",
        "Diary",
        diary or "—",
        "",
        "Summary",
        f"- Expenses total: {expenses_total}",
        f"- Location summary: {location_summary}",
        f"- Mood: {mood}",
        f"- Weight: {weight}",
        "",
        "Expenses (昨日の支出)",
        f"Total: {_format_yen(expenses_total_value)}",
        *expenses_lines,
        "",
        f"🎉 昨日完了したこと（Done: {len(done_items)}）",
        *render_items(done_visible, done_more),
        "",
        f"🧹 昨日手放したこと（Drop: {len(drop_items)}）",
        *render_items(drop_visible, drop_more),
        "",
        "Meal summary",
        f"- {meal_summary}",
        "Meal Photos",
        *([f"- {url}" for url in meal_photos] if meal_photos else ["- —"]),
    ]
    if mood_notes_url:
        lines += [
            "",
            "Mood / Notes",
            mood_notes_url,
        ]
    return "\n".join(lines).strip() + "\n"
