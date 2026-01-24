from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from connectors.tasks import TasksConnector
from delivery.email_templates import build_email_html, build_email_text
from ingest.daily_log_upsert import upsert_daily_log


@dataclass(frozen=True)
class IngestResult:
    summary_html: str
    summary_text: str
    sources: List[str]
    raw_payload: Dict[str, Any]


def ingest_sources(
    *,
    target_date: str,
    page_id: str,
    tasks_closed_url: str,
    daily_log_upsert_url: str,
    bearer_token: Optional[str],
    run_id: str,
    source_label: str,
) -> IngestResult:
    connectors = [TasksConnector(tasks_closed_url, bearer_token)]

    summary_blocks: Dict[str, Any] = {}
    raw_payload: Dict[str, Any] = {}
    sources: List[str] = []

    for connector in connectors:
        result = connector.fetch(target_date)
        rendered = connector.render(result)
        summary_blocks.update(rendered.get("summary_blocks", {}))
        raw_payload[connector.id] = rendered.get("raw_payload", {})
        sources.append(connector.id)

    done_items = summary_blocks.get("done_items", [])
    drop_items = summary_blocks.get("drop_items", [])
    progress_line = summary_blocks.get(
        "progress_line", "昨日の前進：Done 0件 / Drop 0件"
    )

    summary_html = build_email_html(
        date_str=target_date,
        run_id=run_id,
        progress_line=progress_line,
        done_items=done_items,
        drop_items=drop_items,
    )
    summary_text = build_email_text(
        date_str=target_date,
        run_id=run_id,
        progress_line=progress_line,
        done_items=done_items,
        drop_items=drop_items,
    )

    payload = {
        "target_date": target_date,
        "title": f"Daily Log｜{target_date}",
        "summary_text": summary_text,
        "summary_html": summary_html,
        "mail_id": run_id,
        "source": source_label,
        "page_id": page_id,
        "data_json": json.dumps(
            {
                "sources": sources,
                "summary": {
                    "done_items": done_items,
                    "drop_items": drop_items,
                    "progress_line": progress_line,
                },
                "raw": raw_payload,
            },
            ensure_ascii=False,
        ),
    }

    upsert_daily_log(daily_log_upsert_url, payload, bearer_token)

    return IngestResult(
        summary_html=summary_html,
        summary_text=summary_text,
        sources=sources,
        raw_payload=raw_payload,
    )
