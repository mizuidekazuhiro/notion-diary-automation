from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from connectors.expenses import ExpensesConnector
from connectors.health import HealthConnector
from connectors.tasks import TasksConnector
from delivery.email_templates import build_email_html, build_email_text
from ingest.daily_log_upsert import upsert_daily_log


@dataclass(frozen=True)
class IngestResult:
    summary_html: str
    summary_text: str
    sources: List[str]
    raw_payload: Dict[str, Any]


class HealthDataQualityError(RuntimeError):
    """Raised after Phase A records all source results when Health is unusable."""


BLOCKING_HEALTH_STATUSES = {"failed"}
WARNING_HEALTH_STATUSES = {"no_data", "stale", "degraded"}


def ingest_sources(
    *,
    target_date: str,
    page_id: str,
    tasks_closed_url: str,
    health_ingest_url: str,
    expenses_ingest_url: str,
    daily_log_upsert_url: str,
    bearer_token: Optional[str],
    run_id: str,
    source_label: str,
    after_step: Optional[Callable[[str], None]] = None,
) -> IngestResult:
    def _log_patch_summary(endpoint_name: str, payload: Any) -> None:
        payload_dict = payload if isinstance(payload, dict) else {}
        if not payload_dict:
            return
        patch_keys = payload_dict.get("patch_property_keys")
        if not isinstance(patch_keys, list):
            patch_keys = []
        print(
            "PHASE1_CONNECTOR_RESPONSE "
            f"endpoint_name={endpoint_name} "
            f"patch_includes_meal_photos={bool(payload_dict.get('patch_includes_meal_photos', False))} "
            f"meal_photos_files_count={int(payload_dict.get('meal_photos_files_count') or 0)} "
            f"patch_property_keys={patch_keys}"
        )

    connectors = [
        TasksConnector(tasks_closed_url, bearer_token),
        HealthConnector(health_ingest_url, bearer_token),
        ExpensesConnector(expenses_ingest_url, bearer_token),
    ]

    summary_blocks: Dict[str, Any] = {}
    raw_payload: Dict[str, Any] = {}
    sources: List[str] = []
    blocking_health_status: Optional[str] = None
    blocking_health_error_code: Optional[str] = None
    warning_health_status: Optional[str] = None
    warning_health_error_code: Optional[str] = None

    for connector in connectors:
        result = connector.fetch(target_date)
        endpoint_name = {
            "tasks": "tasks",
            "health": "/execute/api/daily_log/ingest_health",
            "expenses": "/execute/api/daily_log/ingest_expenses",
        }.get(connector.id, connector.id)
        result_payload = getattr(result, "payload", None)
        if result_payload is None:
            result_payload = getattr(result, "raw_payload", None)
        _log_patch_summary(endpoint_name, result_payload or {})
        if after_step:
            after_step(f"ingest_{connector.id}")
        rendered = connector.render(result)
        summary_blocks.update(rendered.get("summary_blocks", {}))
        raw_payload[connector.id] = rendered.get("raw_payload", {})
        sources.append(connector.id)
        if connector.id == "health":
            health_status = str(getattr(result, "status", "failed") or "failed").lower()
            if health_status in BLOCKING_HEALTH_STATUSES:
                blocking_health_status = health_status
                blocking_health_error_code = (
                    str(getattr(result, "error_code", "") or "").strip() or None
                )
            elif health_status in WARNING_HEALTH_STATUSES:
                warning_health_status = health_status
                warning_health_error_code = (
                    str(getattr(result, "error_code", "") or "").strip() or None
                )

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

    upsert_response = upsert_daily_log(daily_log_upsert_url, payload, bearer_token)
    _log_patch_summary("/api/daily_log/upsert", upsert_response)
    if after_step:
        after_step("upsert")

    if warning_health_status:
        logging.warning(
            "phase_a_health_quality_warning status=%s error_code=%s processing_continues=true",
            warning_health_status,
            warning_health_error_code or "unknown",
        )

    if blocking_health_status:
        logging.error(
            "phase_a_health_quality_gate_failed status=%s error_code=%s",
            blocking_health_status,
            blocking_health_error_code or "unknown",
        )
        raise HealthDataQualityError(
            "health source is unusable: "
            f"status={blocking_health_status} "
            f"error_code={blocking_health_error_code or 'unknown'}"
        )

    return IngestResult(
        summary_html=summary_html,
        summary_text=summary_text,
        sources=sources,
        raw_payload=raw_payload,
    )
