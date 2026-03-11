from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from ingest.http_client import fetch_json


@dataclass(frozen=True)
class TaskItem:
    page_id: str
    title: str
    priority: Optional[str]


@dataclass(frozen=True)
class TasksResult:
    target_date: str
    done: List[TaskItem]
    drop: List[TaskItem]
    raw_payload: Dict[str, Any]


def _dedupe(items: List[TaskItem]) -> List[TaskItem]:
    seen: set[str] = set()
    deduped: List[TaskItem] = []
    for item in items:
        key = item.page_id or f"{item.title}-{item.priority or '-'}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _format_item(item: TaskItem) -> str:
    priority = item.priority or "-"
    return f"{item.title} (Priority: {priority})"


class TasksConnector:
    id = "tasks"

    def __init__(self, tasks_closed_url: str, bearer_token: Optional[str]) -> None:
        self.tasks_closed_url = tasks_closed_url
        self.bearer_token = bearer_token

    def fetch(self, target_date: str) -> TasksResult:
        query = urlencode({"date": target_date})
        url = f"{self.tasks_closed_url}?{query}"
        payload = fetch_json(url, self.bearer_token)

        done_items = [
            TaskItem(
                page_id=item.get("page_id", ""),
                title=item.get("title", ""),
                priority=item.get("priority"),
            )
            for item in payload.get("done", [])
        ]
        drop_items = [
            TaskItem(
                page_id=item.get("page_id", ""),
                title=item.get("title", ""),
                priority=item.get("priority"),
            )
            for item in payload.get("drop", [])
        ]

        return TasksResult(
            target_date=payload.get("date", target_date),
            done=_dedupe(done_items),
            drop=_dedupe(drop_items),
            raw_payload=payload,
        )

    def render(self, result: TasksResult) -> Dict[str, Any]:
        done_items = [_format_item(item) for item in result.done]
        drop_items = [_format_item(item) for item in result.drop]
        progress_line = f"昨日の前進：Done {len(done_items)}件 / Drop {len(drop_items)}件"

        return {
            "summary_blocks": {
                "done_items": done_items,
                "drop_items": drop_items,
                "progress_line": progress_line,
            },
            "raw_payload": {
                "date": result.target_date,
                "done": [item.__dict__ for item in result.done],
                "drop": [item.__dict__ for item in result.drop],
            },
        }
