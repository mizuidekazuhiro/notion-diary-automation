from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PhaseCDeps:
    refresh_summary: Callable[[Any, str], Any | None]
    run_weather: Callable[[Any], Any]
    run_expense_f: Callable[[Any], dict[str, Any]]
    run_sleep: Callable[[Any], Any]
    run_notes_label: Callable[[Any], Any]
    run_f_risk: Callable[[Any], Any]
    run_today_advice: Callable[[Any], Any]
    run_diary: Callable[[Any], Any]
    run_notify: Callable[[Any], bool | dict[str, Any]]
    mark_notified: Callable[[str], None]


def _run_optional_enrichment(
    step_name: str,
    fn: Callable[[Any], Any],
    summary: Any,
    run_id: str,
    step_status: dict[str, str],
) -> Any:
    try:
        out = fn(summary)
        step_status[step_name] = "success"
        return out
    except Exception as exc:  # noqa: BLE001
        logging.exception(
            "phase_c_optional_step_failed target_date(JST)=%s run_id=%s step=%s exception_class=%s exception_message=%s failing_stage=%s",
            getattr(summary, "target_date", "unknown"),
            run_id,
            step_name,
            exc.__class__.__name__,
            str(exc),
            step_name,
        )
        step_status[step_name] = "failed"
        return summary


def run_phase_c(config: Any, *, target_date: str, run_id: str, deps: PhaseCDeps) -> None:
    logging.info("phase_c_start target_date(JST)=%s run_id=%s", target_date, run_id)
    step_names = ["weather", "expense_f", "sleep", "notes_label", "f_risk", "today_advice", "diary", "notify", "mail_metadata", "study"]
    step_status: dict[str, str] = {k: "not_applicable" for k in step_names}
    summary = deps.refresh_summary(config, target_date)
    if not summary:
        step_status["diary"] = "failed"
        logging.info("phase_c_step_summary target_date(JST)=%s run_id=%s step_status=%s", target_date, run_id, step_status)
        raise RuntimeError(
            "PhaseC fail: Daily_Log summary not found "
            f"for target_date(JST)={target_date} run_id={run_id}"
        )

    summary = _run_optional_enrichment("weather", deps.run_weather, summary, run_id, step_status)
    summary = deps.refresh_summary(config, summary.target_date) or summary

    try:
        expense_f_alert = deps.run_expense_f(summary)
    except Exception as exc:  # noqa: BLE001
        logging.exception(
            "phase_c_optional_step_failed target_date(JST)=%s run_id=%s step=expense_f exception_class=%s exception_message=%s failing_stage=expense_f",
            summary.target_date,
            run_id,
            exc.__class__.__name__,
            str(exc),
        )
        expense_f_alert = {
            "matched": False,
            "title": "Expense F",
            "summary": "",
            "reasons": [],
            "debug": {"error": str(exc)},
        }
        step_status["expense_f"] = "failed"
    else:
        step_status["expense_f"] = "success"

    summary = deps.refresh_summary(config, summary.target_date) or summary
    summary = _run_optional_enrichment("sleep", deps.run_sleep, summary, run_id, step_status)
    summary = deps.refresh_summary(config, summary.target_date) or summary
    summary = _run_optional_enrichment("notes_label", deps.run_notes_label, summary, run_id, step_status)
    summary = deps.refresh_summary(config, summary.target_date) or summary
    summary = _run_optional_enrichment("f_risk", deps.run_f_risk, summary, run_id, step_status)
    summary = deps.refresh_summary(config, summary.target_date) or summary

    try:
        summary = deps.run_today_advice(summary)
        step_status["today_advice"] = "success"
    except Exception:
        step_status["today_advice"] = "failed"
        logging.info("phase_c_step_summary target_date(JST)=%s run_id=%s step_status=%s", target_date, run_id, step_status)
        raise

    summary = deps.refresh_summary(config, summary.target_date) or summary
    if not (getattr(summary, "today_advice", "") or "").strip():
        raise RuntimeError(f"PhaseC fail: Today advice is empty for target_date={summary.target_date}")
    try:
        summary = deps.run_diary(summary)
        step_status["diary"] = "success"
    except Exception:
        step_status["diary"] = "failed"
        logging.info("phase_c_step_summary target_date(JST)=%s run_id=%s step_status=%s", target_date, run_id, step_status)
        raise

    summary = deps.refresh_summary(config, summary.target_date) or summary
    if not (summary.diary or "").strip():
        step_status["diary"] = "failed"
        logging.info("phase_c_step_summary target_date(JST)=%s run_id=%s step_status=%s", summary.target_date, run_id, step_status)
        raise RuntimeError(f"PhaseC fail: Diary is empty for target_date={summary.target_date}")

    if expense_f_alert.get("matched"):
        logging.info(
            "phase_c_notify_expense_f_alert target_date(JST)=%s run_id=%s matched=%s reasons=%s",
            summary.target_date,
            run_id,
            expense_f_alert.get("matched"),
            (expense_f_alert.get("reasons") or [])[:3],
        )

    notify_result = deps.run_notify(summary)
    sent = bool(notify_result)
    already_marked = False
    if isinstance(notify_result, dict):
        sent = bool(notify_result.get("sent"))
        already_marked = bool(notify_result.get("already_marked"))

    if sent:
        if not already_marked:
            deps.mark_notified(summary.target_date)
        logging.info(
            "phase_c_notify_sent target_date(JST)=%s run_id=%s notified_updated=%s",
            summary.target_date,
            run_id,
            True,
        )
        step_status["notify"] = "success"
    else:
        step_status["notify"] = "skipped"

    logging.info("phase_c_step_summary target_date(JST)=%s run_id=%s step_status=%s", summary.target_date, run_id, step_status)
    logging.info("phase_c_end target_date(JST)=%s run_id=%s", summary.target_date, run_id)
