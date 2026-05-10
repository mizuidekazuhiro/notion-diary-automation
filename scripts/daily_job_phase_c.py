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


def _run_optional_enrichment(step_name: str, fn: Callable[[Any], Any], summary: Any, run_id: str, status: dict[str,str]) -> Any:
    try:
        out = fn(summary)
        status[step_name] = "success"
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
        status[step_name] = "failed"
        return summary


def run_phase_c(config: Any, *, target_date: str, run_id: str, deps: PhaseCDeps) -> None:
    logging.info("phase_c_start target_date(JST)=%s run_id=%s", target_date, run_id)
    summary = deps.refresh_summary(config, target_date)
    if not summary:
        logging.info(
            "phase_c_sleep_saved target_date(JST)=%s run_id=%s updated=%s skip_reason=no_daily_log generated_properties=[]",
            target_date,
            run_id,
            False,
        )
        return

    step_status = {k: "not_applicable" for k in ["weather", "sleep", "notes_label", "f_risk", "today_advice", "diary", "notify", "mail_metadata", "study"]}

    summary = _run_optional_enrichment("weather", deps.run_weather, summary, run_id, step_status)
    summary = deps.refresh_summary(config, summary.target_date) or summary

    try:
        expense_f_alert = deps.run_expense_f(summary)
        step_status["study"] = "not_applicable"
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
            "title": "望ましくない支出（Fプロパティ）",
            "summary": "",
            "reasons": [],
            "debug": {"error": str(exc)},
        }

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
        raise
    summary = deps.refresh_summary(config, summary.target_date) or summary
    try:
        summary = deps.run_diary(summary)
        step_status["diary"] = "success"
    except Exception:
        step_status["diary"] = "failed"
        raise
    summary = deps.refresh_summary(config, summary.target_date) or summary

    if not (summary.diary or "").strip():
        logging.info(
            "phase_c_notify_skipped target_date(JST)=%s run_id=%s skip_reason=no_daily_log",
            summary.target_date,
            run_id,
        )
        step_status["notify"] = "skipped"
        logging.info("phase_c_step_summary target_date(JST)=%s run_id=%s weather_%s sleep_%s notes_label_%s f_risk_%s today_advice_%s diary_%s notify_%s mail_metadata_%s study_%s", summary.target_date, run_id, step_status.get("weather"), step_status.get("sleep"), step_status.get("notes_label"), step_status.get("f_risk"), step_status.get("today_advice"), step_status.get("diary"), step_status.get("notify"), step_status.get("mail_metadata"), step_status.get("study"))
        return

    if expense_f_alert.get("matched"):
        logging.info(
            "phase_c_notify_expense_f_alert target_date(JST)=%s run_id=%s matched=%s reasons=%s",
            summary.target_date,
            run_id,
            expense_f_alert.get("matched"),
            (expense_f_alert.get("reasons") or [])[:3],
        )

    notify_result = deps.run_notify(summary)
    step_status["notify"] = "success"
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

    logging.info("phase_c_step_summary target_date(JST)=%s run_id=%s weather_%s sleep_%s notes_label_%s f_risk_%s today_advice_%s diary_%s notify_%s mail_metadata_%s study_%s", summary.target_date, run_id, step_status.get("weather"), step_status.get("sleep"), step_status.get("notes_label"), step_status.get("f_risk"), step_status.get("today_advice"), step_status.get("diary"), step_status.get("notify"), step_status.get("mail_metadata"), step_status.get("study"))
    logging.info("phase_c_end target_date(JST)=%s run_id=%s", summary.target_date, run_id)
