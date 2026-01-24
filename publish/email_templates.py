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
    expenses_total = _normalize_number(
        payload.get("expenses_total") if isinstance(payload, Mapping) else None
    )
    location_summary = _normalize_text(
        payload.get("location_summary") if isinstance(payload, Mapping) else None
    )
    mood = _normalize_text(payload.get("mood") if isinstance(payload, Mapping) else None)
    weight = _normalize_number(payload.get("weight") if isinstance(payload, Mapping) else None)

    diary_html = html.escape(diary).replace("\n", "<br />")
    location_html = html.escape(location_summary).replace("\n", "<br />")

    done_rows = _render_task_rows(done_visible) + _render_more_row(done_more)
    drop_rows = _render_task_rows(drop_visible) + _render_more_row(drop_more)

    return f"""\
<!DOCTYPE html>
<html lang=\"ja\">
  <head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Daily Log | {html.escape(target_date)}</title>
  </head>
  <body style=\"margin: 0; padding: 0; background-color: #f6f7f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #111827;\">
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
                      <h2 style=\"margin: 0 0 12px 0; font-size: 16px;\">Summary</h2>
                      <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">
                        <tr>
                          <td style=\"padding: 6px 0; font-size: 13px; color: #6b7280; width: 160px;\">Diary</td>
                          <td style=\"padding: 6px 0; font-size: 14px; color: #111827;\">{diary_html}</td>
                        </tr>
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
    expenses_total = _normalize_number(
        payload.get("expenses_total") if isinstance(payload, Mapping) else None
    )
    location_summary = _normalize_text(
        payload.get("location_summary") if isinstance(payload, Mapping) else None
    )
    mood = _normalize_text(payload.get("mood") if isinstance(payload, Mapping) else None)
    weight = _normalize_number(payload.get("weight") if isinstance(payload, Mapping) else None)

    lines = [
        f"Daily Log | {target_date}",
        f"Run ID: {run_id}",
        "",
        f"🎉 昨日完了したこと（Done: {len(done_items)}）",
        *render_items(done_visible, done_more),
        "",
        f"🧹 昨日手放したこと（Drop: {len(drop_items)}）",
        *render_items(drop_visible, drop_more),
        "",
        "Summary",
        f"- Diary: {diary}",
        f"- Expenses total: {expenses_total}",
        f"- Location summary: {location_summary}",
        f"- Mood: {mood}",
        f"- Weight: {weight}",
    ]
    return "\n".join(lines).strip() + "\n"
