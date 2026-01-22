import json
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
TASK_STATUS_DO = os.getenv("TASK_STATUS_DO", "Do")
TASK_STATUS_SOMEDAY = os.getenv("TASK_STATUS_SOMEDAY", "Someday")


@dataclass
class Config:
    mail_from: str
    mail_to: List[str]
    gmail_app_password: str
    inbox_url: str
    tasks_url: str
    tasks_closed_url: str
    daily_log_url: str
    bearer_token: Optional[str]


@dataclass
class TaskItem:
    title: str
    status: Optional[str]
    priority: Optional[str]
    since_do: Optional[str]
    confirm_promote_url: Optional[str]


@dataclass
class InboxItem:
    title: str


@dataclass
class ClosedTaskItem:
    page_id: str
    title: str
    priority: Optional[str]
    closed_date: Optional[str]


@dataclass
class ClosedTasks:
    date: Optional[str]
    done: List[ClosedTaskItem]
    drop: List[ClosedTaskItem]


def load_config() -> Config:
    def require(name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise RuntimeError(f"Missing env var: {name}")
        return value

    mail_to_raw = require("MAIL_TO")
    mail_to = [item.strip() for item in mail_to_raw.split(",") if item.strip()]

    return Config(
        mail_from=require("MAIL_FROM"),
        mail_to=mail_to,
        gmail_app_password=require("GMAIL_APP_PASSWORD"),
        inbox_url=require("INBOX_JSON_URL"),
        tasks_url=require("TASKS_JSON_URL"),
        tasks_closed_url=require("TASKS_CLOSED_URL"),
        daily_log_url=require("DAILY_LOG_UPSERT_URL"),
        bearer_token=os.getenv("WORKERS_BEARER_TOKEN"),
    )


def fetch_json(url: str, bearer_token: Optional[str]) -> Dict[str, Any]:
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def post_json(url: str, payload: Dict[str, Any], bearer_token: Optional[str]) -> None:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()


def parse_tasks(data: Dict[str, Any]) -> List[TaskItem]:
    items = data.get("items", [])
    tasks = []
    for item in items:
        tasks.append(
            TaskItem(
                title=item.get("title", ""),
                status=item.get("status"),
                priority=item.get("priority"),
                since_do=item.get("since_do"),
                confirm_promote_url=item.get("confirm_promote_url"),
            )
        )
    return tasks


def parse_inbox(data: Dict[str, Any]) -> List[InboxItem]:
    items = data.get("items", [])
    return [InboxItem(title=item.get("title", "")) for item in items]


def parse_closed_tasks(data: Dict[str, Any]) -> ClosedTasks:
    def parse_items(items: List[Dict[str, Any]], date_key: str) -> List[ClosedTaskItem]:
        parsed: List[ClosedTaskItem] = []
        for item in items:
            parsed.append(
                ClosedTaskItem(
                    page_id=item.get("page_id", ""),
                    title=item.get("title", ""),
                    priority=item.get("priority"),
                    closed_date=item.get(date_key),
                )
            )
        return parsed

    done_items = parse_items(data.get("done", []), "done_date")
    drop_items = parse_items(data.get("drop", []), "drop_date")
    return ClosedTasks(date=data.get("date"), done=done_items, drop=drop_items)


def fetch_closed_tasks_safe(url: str, bearer_token: Optional[str]) -> ClosedTasks:
    try:
        data = fetch_json(url, bearer_token)
        return parse_closed_tasks(data)
    except Exception:
        return ClosedTasks(date=None, done=[], drop=[])


def days_since(date_str: Optional[str], today: datetime) -> Optional[int]:
    if not date_str:
        return None
    try:
        since_date = datetime.fromisoformat(date_str).date()
    except ValueError:
        return None
    return (today.date() - since_date).days


def build_activity_summary(tasks: List[TaskItem], inbox: List[InboxItem]) -> str:
    now = datetime.now(JST)
    lines: List[str] = []

    lines.append("【Tasks (Status: Do)】")
    do_tasks = [task for task in tasks if task.status == TASK_STATUS_DO]
    if do_tasks:
        for task in do_tasks:
            elapsed = days_since(task.since_do, now)
            elapsed_text = f"{elapsed} days" if elapsed is not None else "-"
            priority = task.priority or "-"
            lines.append(f"- {task.title} (Priority: {priority}, Since Do: {elapsed_text})")
    else:
        lines.append("- None")

    lines.append("")
    lines.append("【Inbox】")
    if inbox:
        for item in inbox:
            lines.append(f"- {item.title}")
    else:
        lines.append("- None")

    monday = now.weekday() == 0
    if monday:
        lines.append("")
        lines.append("【Someday (Monday only)】")
        someday_tasks = [task for task in tasks if task.status == TASK_STATUS_SOMEDAY]
        if someday_tasks:
            for task in someday_tasks:
                link = task.confirm_promote_url or "Confirm URL unavailable"
                lines.append(f"- {task.title} (Promote: {link})")
        else:
            lines.append("- None")

    return "\n".join(lines)


def build_email_html(
    tasks: List[TaskItem],
    inbox: List[InboxItem],
    closed_tasks: ClosedTasks,
    activity_summary: str,
    run_id: str,
) -> str:
    now = datetime.now(JST)
    date_str = now.strftime("%Y-%m-%d")

    def list_items(items: List[str]) -> str:
        if not items:
            return "<li>None</li>"
        return "".join(f"<li>{item}</li>" for item in items)

    def format_closed_items(items: List[ClosedTaskItem], icon: str) -> List[str]:
        formatted: List[str] = []
        for item in items:
            if item.priority:
                formatted.append(f"{icon} {item.title} (Priority: {item.priority})")
            else:
                formatted.append(f"{icon} {item.title}")
        return formatted

    def render_closed_section(
        title: str, label: str, icon: str, items: List[ClosedTaskItem]
    ) -> str:
        formatted_items = format_closed_items(items, icon)
        preview_items = formatted_items[:3]
        preview_html = ""
        if preview_items:
            preview_html = f"""
            <div>
              <p style="margin: 8px 0 4px 0;">Preview:</p>
              <ul>
                {list_items(preview_items)}
              </ul>
            </div>
            """
        return f"""
        <details>
          <summary>{title}（{label}: {len(items)}）</summary>
          {preview_html}
          <ul>
            {list_items(formatted_items)}
          </ul>
        </details>
        """

    task_items = [
        f"{task.title} (Priority: {task.priority or '-'}, Since Do: {days_since(task.since_do, now) or '-'})"
        for task in tasks
        if task.status == TASK_STATUS_DO
    ]
    inbox_items = [item.title for item in inbox]
    done_items = closed_tasks.done
    drop_items = closed_tasks.drop
    progress_line = f"昨日の前進：Done {len(done_items)}件 / Drop {len(drop_items)}件"

    return f"""
    <html>
      <body>
        <h2>{date_str} Daily Summary</h2>
        <p>Run ID: {run_id}</p>
        {render_closed_section("🎉 昨日完了したこと", "Done", "✅", done_items)}
        {render_closed_section("🧹 昨日手放したこと", "Drop", "🧹", drop_items)}
        <p><strong>{progress_line}</strong></p>
        <h3>Tasks (Status: Do)</h3>
        <ul>
          {list_items(task_items)}
        </ul>
        <h3>Inbox</h3>
        <ul>
          {list_items(inbox_items)}
        </ul>
        <h3>Activity Summary</h3>
        <pre style="white-space: pre-wrap;">{activity_summary}</pre>
      </body>
    </html>
    """


def send_email(config: Config, subject: str, html_body: str) -> None:
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = config.mail_from
    message["To"] = ", ".join(config.mail_to)

    message.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(config.mail_from, config.gmail_app_password)
        server.sendmail(config.mail_from, config.mail_to, message.as_string())


def main() -> None:
    config = load_config()
    bearer = config.bearer_token

    tasks_data = fetch_json(config.tasks_url, bearer)
    inbox_data = fetch_json(config.inbox_url, bearer)
    closed_tasks = fetch_closed_tasks_safe(config.tasks_closed_url, bearer)

    tasks = parse_tasks(tasks_data)
    inbox = parse_inbox(inbox_data)

    activity_summary = build_activity_summary(tasks, inbox)

    now = datetime.now(JST)
    target_date = now.strftime("%Y-%m-%d")
    title = f"{target_date} Daily Log"

    run_id = os.getenv("GITHUB_RUN_ID", "local")

    upsert_payload = {
        "target_date": target_date,
        "title": title,
        "activity_summary": activity_summary,
        "mail_id": run_id,
        "source": "automation",
        "data_json": json.dumps(
            {
                "tasks": [task.__dict__ for task in tasks],
                "inbox": [item.__dict__ for item in inbox],
                "closed_tasks": {
                    "date": closed_tasks.date,
                    "done": [item.__dict__ for item in closed_tasks.done],
                    "drop": [item.__dict__ for item in closed_tasks.drop],
                },
            },
            ensure_ascii=False,
        ),
    }

    post_json(config.daily_log_url, upsert_payload, bearer)

    subject = f"[Daily Log] {target_date}"
    html_body = build_email_html(tasks, inbox, closed_tasks, activity_summary, run_id)
    send_email(config, subject, html_body)


if __name__ == "__main__":
    main()
