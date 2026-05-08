from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping

DEFAULT_TODAY_ADVICE_MIN_CHARS = 220
DEFAULT_TODAY_ADVICE_MAX_CHARS = 380

GENERIC_PHRASES = (
    "バランスの良い食事",
    "適度に休憩",
    "無理せず",
    "効率的な作業を心がけ",
)
TREND_TERMS = ("直近", "7日", "過去", "傾向", "高評価", "低評価", "good", "bad")
NON_SLEEP_TERMS = ("行動", "記録", "学習", "勉強", "タスク", "食事", "支出", "notes", "場所")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _get(summary: object, name: str, default: object = None) -> object:
    return getattr(summary, name, default)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _compact_len(value: str) -> int:
    return len(re.sub(r"\s+", "", value or ""))


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _section_present(mail_text: str, *labels: str) -> bool:
    lowered = mail_text.lower()
    return any(label.lower() in lowered for label in labels)


def _has_numeric(summary: object, *names: str) -> bool:
    return any(_safe_float(_get(summary, name)) is not None for name in names)


def _has_text(summary: object, *names: str) -> bool:
    return any(bool(_text(_get(summary, name))) for name in names)


def _add_issue(issues: list[dict[str, object]], code: str, severity: str, message: str, suggestion: str = "") -> None:
    issues.append({"code": code, "severity": severity, "message": message, "suggestion": suggestion})


def build_quality_report(
    summary: object | None,
    *,
    mail_plain_text: str,
    mail_html: str,
    run_id: str = "",
    run_url: str = "",
) -> dict[str, object]:
    min_chars = _env_int("DAILY_MAIL_TODAY_ADVICE_MIN_CHARS", DEFAULT_TODAY_ADVICE_MIN_CHARS)
    max_chars = _env_int("DAILY_MAIL_TODAY_ADVICE_MAX_CHARS", DEFAULT_TODAY_ADVICE_MAX_CHARS)
    issues: list[dict[str, object]] = []

    if summary is None:
        _add_issue(
            issues,
            "daily_log_summary_missing",
            "error",
            "Daily Log summary could not be read, so the mail output could not be evaluated.",
            "Check DAILY_LOG_UPSERT_URL, WORKERS_BEARER_TOKEN, and the target_date used by Phase D.",
        )
        return _finalize("", run_id, run_url, issues, {}, {})

    target_date = _text(_get(summary, "target_date")) or _text(_get(summary, "date"))
    today_advice = _text(_get(summary, "today_advice"))
    today_advice_chars = _compact_len(today_advice)

    if not _text(mail_plain_text):
        _add_issue(issues, "mail_plain_text_empty", "error", "Rendered mail plain text is empty.")
    if not _text(mail_html):
        _add_issue(issues, "mail_html_empty", "warning", "Rendered mail HTML is empty.")

    if not today_advice:
        _add_issue(issues, "today_advice_missing", "error", "Today advice is missing from the Daily Log summary.")
    else:
        if today_advice_chars < min_chars or today_advice_chars > max_chars:
            _add_issue(issues, "today_advice_length_out_of_range", "warning", f"Today advice length is {today_advice_chars}; expected {min_chars}-{max_chars} compact chars.")
        if not _has_any(today_advice, TREND_TERMS):
            _add_issue(issues, "today_advice_missing_trend_evidence", "warning", "Today advice does not appear to mention recent or historical trend evidence.")
        if not _has_any(today_advice, NON_SLEEP_TERMS):
            _add_issue(issues, "today_advice_sleep_only_risk", "warning", "Today advice may be sleep-only and may not connect to historical behavior patterns.")
        matched_generic = [phrase for phrase in GENERIC_PHRASES if phrase in today_advice]
        if matched_generic:
            _add_issue(issues, "today_advice_generic_phrase", "warning", f"Today advice contains generic phrase(s): {', '.join(matched_generic)}")

    sections = {
        "today_advice": _section_present(mail_plain_text, "Today advice"),
        "study": _section_present(mail_plain_text, "司法試験 Study", "勉強時間"),
        "sleep": _section_present(mail_plain_text, "Sleep & Condition", "睡眠時間", "Sleep Analysis JP"),
        "weather": _section_present(mail_plain_text, "Weather"),
        "diary": _section_present(mail_plain_text, "Diary"),
        "meal": _section_present(mail_plain_text, "Meal", "食事"),
    }

    if today_advice and not sections["today_advice"]:
        _add_issue(issues, "today_advice_not_rendered", "error", "Today advice exists but is not present in rendered mail text.")

    study_present = _has_numeric(summary, "study_minutes", "study_sessions") or _has_text(summary, "study_last_used_at")
    if study_present and not sections["study"]:
        _add_issue(issues, "study_not_rendered", "error", "Study data exists but the rendered mail does not show the study section.")

    sleep_present = _has_numeric(summary, "resolved_sleep_duration_min", "sleep_duration_min", "sleep_score", "readiness_stars", "sleep_heart_rate") or _has_text(summary, "sleep_start", "sleep_end", "sleep_analysis_jp", "today_condition_forecast_jp")
    if sleep_present and not sections["sleep"]:
        _add_issue(issues, "sleep_not_rendered", "error", "Sleep/condition data exists but the rendered mail does not show the sleep section.")

    weather_present = _has_text(summary, "weather_summary", "weather_location") or _has_numeric(summary, "weather_code", "weather_temp_max_c", "weather_temp_min_c")
    if weather_present and not sections["weather"]:
        _add_issue(issues, "weather_not_rendered", "error", "Weather data exists but the rendered mail does not show the weather section.")

    if not _text(_get(summary, "diary")):
        _add_issue(issues, "diary_missing", "warning", "Diary text is missing from the Daily Log summary.")
    elif not sections["diary"]:
        _add_issue(issues, "diary_not_rendered", "error", "Diary exists but is not present in rendered mail text.")

    if _text(_get(summary, "meal_summary")) and not sections["meal"]:
        _add_issue(issues, "meal_not_rendered", "warning", "Meal summary exists but the rendered mail does not appear to include the meal section.")

    location_summary = _text(_get(summary, "location_summary"))
    location_summary_source = _text(_get(summary, "location_summary_source")) or "empty"
    location_rendered = ("Location summary" in (mail_plain_text or "")) and (location_summary in (mail_plain_text or "") if location_summary else True)
    if location_summary and not location_rendered:
        _add_issue(issues, "location_summary_not_rendered", "error", "Location summary exists but is not present in rendered mail text.")
    if location_summary and location_summary_source == "location_summary_gpt" and not location_rendered:
        _add_issue(issues, "location_summary_gpt_not_rendered", "error", "Location summary (GPT) exists but is not present in rendered mail text.")

    meal_photos = _get(summary, "meal_photos") if isinstance(_get(summary, "meal_photos"), list) else []
    meal_photos_count = len(meal_photos)
    meal_photos_rendered = meal_photos_count == 0 or any(url in (mail_plain_text or "") for url in meal_photos) or ("<img " in (mail_html or "") and "Meal photo" in (mail_html or ""))
    if meal_photos_count > 0 and not meal_photos_rendered:
        _add_issue(issues, "meal_photos_not_rendered", "error", "Meal photos exist but are not rendered in HTML or text mail.")

    invalid_img_src_count = len(re.findall(r'<img[^>]+src="[^"]*(?:file://|permissionrecord=|notion\\.so/image/)[^"]*"', mail_html or "", re.IGNORECASE))
    if invalid_img_src_count > 0:
        _add_issue(issues, "meal_photo_invalid_img_src", "error", "Mail HTML contains invalid meal photo image source URL(s).")

    snapshot_raw = _text(_get(summary, "mail_input_snapshot_json"))
    snapshot_has_meal_photos = False
    snapshot_has_location_summary = False
    if snapshot_raw:
        try:
            snapshot = json.loads(snapshot_raw)
            if isinstance(snapshot, Mapping):
                snapshot_has_meal_photos = "meal_photos" in snapshot
                snapshot_has_location_summary = "location_summary" in snapshot
        except json.JSONDecodeError:
            pass
    if not snapshot_has_meal_photos:
        _add_issue(issues, "mail_snapshot_missing_meal_photos", "error", "mail_input_snapshot_json does not include meal_photos.")
    if not snapshot_has_location_summary:
        _add_issue(issues, "mail_snapshot_missing_location_summary", "error", "mail_input_snapshot_json does not include location_summary.")

    metrics = {
        "today_advice_chars_compact": today_advice_chars,
        "today_advice_min_chars": min_chars,
        "today_advice_max_chars": max_chars,
        "mail_plain_text_chars": len(mail_plain_text or ""),
        "mail_html_chars": len(mail_html or ""),
        "study_present": study_present,
        "sleep_present": sleep_present,
        "weather_present": weather_present,
        "location_summary_present": bool(location_summary),
        "location_summary_source": location_summary_source,
        "location_rendered": location_rendered,
        "meal_photos_count": meal_photos_count,
        "meal_photos_rendered": meal_photos_rendered,
        "meal_photo_invalid_img_src_count": invalid_img_src_count,
        "mail_snapshot_has_meal_photos": snapshot_has_meal_photos,
        "mail_snapshot_has_location_summary": snapshot_has_location_summary,
    }
    return _finalize(target_date, run_id, run_url, issues, metrics, sections)


def _finalize(target_date: str, run_id: str, run_url: str, issues: list[dict[str, object]], metrics: Mapping[str, object], sections: Mapping[str, object]) -> dict[str, object]:
    error_count = sum(1 for issue in issues if issue.get("severity") == "error")
    warning_count = sum(1 for issue in issues if issue.get("severity") == "warning")
    status = "fail" if error_count else "warning" if warning_count else "pass"
    return {
        "status": status,
        "target_date": target_date,
        "run_id": run_id,
        "run_url": run_url,
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
        "metrics": dict(metrics),
        "section_presence": dict(sections),
        "privacy": {"full_mail_body_saved": False, "default_body_artifacts": "disabled"},
    }


def build_markdown_report(report: Mapping[str, object]) -> str:
    lines = [
        f"# Daily mail quality report - {report.get('target_date') or 'unknown'}",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Errors: `{report.get('error_count', 0)}`",
        f"- Warnings: `{report.get('warning_count', 0)}`",
        f"- Run ID: `{report.get('run_id') or ''}`",
    ]
    if report.get("run_url"):
        lines.append(f"- Run URL: {report.get('run_url')}")
    lines.extend(["", "## Metrics", ""])
    metrics = report.get("metrics") if isinstance(report.get("metrics"), Mapping) else {}
    for key, value in sorted(metrics.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Section presence", ""])
    sections = report.get("section_presence") if isinstance(report.get("section_presence"), Mapping) else {}
    for key, value in sorted(sections.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Issues", ""])
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        for issue in issues:
            if not isinstance(issue, Mapping):
                continue
            lines.extend([
                f"### `{issue.get('code')}` ({issue.get('severity')})",
                "",
                str(issue.get("message") or ""),
                "",
            ])
            suggestion = str(issue.get("suggestion") or "").strip()
            if suggestion:
                lines.extend([f"Suggested fix: {suggestion}", ""])
    else:
        lines.append("No issues detected.")
    lines.extend(["", "## Privacy", "", "Full mail body is not saved by default."])
    return "\n".join(lines).rstrip() + "\n"


def write_quality_artifacts(report: Mapping[str, object], *, artifact_dir: str | Path) -> None:
    output_dir = Path(artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "quality_report.md").write_text(build_markdown_report(report), encoding="utf-8")
