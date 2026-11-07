"""Assess classifier quality and quarantine unreliable labels before features.

No classifier or network calls occur here. The returned labels and audit
metrics keep successful parsing distinct from missing/failed classification.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence, TypedDict

from publish.read_daily_log import DailyLogSummary
from scripts.note_batch_labeler import NoteLabel
from scripts.today_advice_context import _safe_text
from scripts.today_advice_audit import TodayAdviceAuditLogger, safe_json

MIN_NOTES_PARSE_RATE = 0.8
MIN_NOTES_DATE_MERGE_RATE = 0.8
MAX_NOTES_UNKNOWN_RATE = 0.4
LOW_QUALITY_PARSE_RATE = 0.5
HIGH_UNKNOWN_WARNING_RATE = 0.9


class NotesQualityMetrics(TypedDict, total=False):
    total_count: int
    non_empty_count: int
    api_calls: int
    labeled_count: int
    notes_parse_success_rate: float
    notes_date_merge_success_rate: float
    unknown_rate: float
    notes_quality_used: Literal["high", "low"]
    notes_label_quality_low: bool
    exclusion_reason: list[str]
    dataframe_sentiment_counts: dict[str, int]
    dataframe_flag_counts: dict[str, int]
    top_tags: list[tuple[str, int]]
    source_grounding_rejections: dict[str, int]
    outcome_validation_status: str
    outcome_validation_calls: int
    outcome_validation_errors: list[str]
    outcome_validation_model: str
    classification_status: str
    classification_failed_count: int


@dataclass(frozen=True)
class NotesQualityResult:
    labels: dict[str, NoteLabel]
    audit_metrics: NotesQualityMetrics


def assess_notes_quality(
    *, historical_summaries: Sequence[DailyLogSummary],
    note_labels: dict[str, NoteLabel], note_label_audit: Mapping[str, Any],
    target_date: str, audit: TodayAdviceAuditLogger,
) -> NotesQualityResult:
    notes_total_count = len(historical_summaries)
    notes_non_empty_count = sum(1 for item in historical_summaries if _safe_text(item.notes))
    notes_labeled = list(note_labels.values())
    notes_parse_success_rate = float(note_label_audit.get("notes_parse_success_rate", 0.0) or 0.0)
    notes_date_merge_success_rate = float(note_label_audit.get("notes_date_merge_success_rate", 0.0) or 0.0)
    notes_unknown_rate = float(note_label_audit.get("unknown_rate", note_label_audit.get("notes_unknown_rate", 0.0)) or 0.0)
    notes_signals_detected_count = int(note_label_audit.get("signals_detected_count", 0) or 0)
    notes_payload = {
        "total_count": notes_total_count,
        "non_empty_count": notes_non_empty_count,
        "api_calls": note_label_audit.get("api_calls", 0),
        "labeled_count": len(notes_labeled),
        "fallback_unknown_count": sum(1 for item in notes_labeled if item.confidence == "low" and item.sentiment_label == "unknown"),
        "dataframe_sentiment_counts": {
            "positive": sum(1 for item in notes_labeled if item.sentiment_label == "positive"),
            "neutral": sum(1 for item in notes_labeled if item.sentiment_label == "neutral"),
            "negative": sum(1 for item in notes_labeled if item.sentiment_label == "negative"),
        },
        "dataframe_flag_counts": {
            "fatigue": sum(1 for item in notes_labeled if item.fatigue_flag),
            "stress": sum(1 for item in notes_labeled if item.stress_flag),
            "social_load": sum(1 for item in notes_labeled if item.social_load_flag),
            "achievement": sum(1 for item in notes_labeled if item.achievement_flag),
            "self_care": sum(1 for item in notes_labeled if item.self_care_flag),
            "sleep_issue": sum(1 for item in notes_labeled if item.sleep_issue_flag),
        },
        "raw_sentiment_counts": note_label_audit.get("raw_sentiment_counts", {}),
        "raw_flag_counts": note_label_audit.get("raw_flag_counts", {}),
        "normalized_sentiment_counts": note_label_audit.get("normalized_sentiment_counts", {}),
        "normalized_flag_counts": note_label_audit.get("normalized_flag_counts", {}),
        "top_evidence_keywords": [k for k, _v in sorted({kw: sum(kw in item.evidence_keywords for item in notes_labeled) for item in notes_labeled for kw in item.evidence_keywords}.items(), key=lambda p: p[1], reverse=True)[:5]],
        "fallback_reason_counts": note_label_audit.get("fallback_reason_counts", {}),
        "raw_response_paths": note_label_audit.get("raw_response_paths", []),
        "notes_classifier_success_rate": note_label_audit.get("notes_classifier_success_rate", 0.0),
        "notes_parse_success_rate": notes_parse_success_rate,
        "notes_date_merge_success_rate": notes_date_merge_success_rate,
        "notes_merge_failed_count": int(note_label_audit.get("notes_merge_failed_count", 0) or 0),
        "unknown_rate": notes_unknown_rate,
        "tag_extract_failed_count": note_label_audit.get("tag_extract_failed_count", 0),
        "parse_low_confidence_count": note_label_audit.get("parse_low_confidence_count", 0),
        "top_tags": note_label_audit.get("top_tags", []),
        "matched_dates_count": note_label_audit.get("matched_dates_count", 0),
        "matched_dates": note_label_audit.get("matched_dates", []),
        "unmatched_input_dates": note_label_audit.get("unmatched_input_dates", []),
        "unmatched_response_dates": note_label_audit.get("unmatched_response_dates", []),
        "signals_detected_count": notes_signals_detected_count,
        "notes_label_quality_low": bool(notes_parse_success_rate < LOW_QUALITY_PARSE_RATE),
        "notes_unmatched_input_dates_count": int(note_label_audit.get("notes_unmatched_input_dates_count", 0) or 0),
        "notes_unmatched_response_dates_count": int(note_label_audit.get("notes_unmatched_response_dates_count", 0) or 0),
        "merge_failed_reason": note_label_audit.get("merge_failed_reason"),
        "source_grounding_rejections": note_label_audit.get("source_grounding_rejections", {}),
        "outcome_validation_status": note_label_audit.get("outcome_validation_status", "skipped"),
        "outcome_validation_calls": note_label_audit.get("outcome_validation_calls", 0),
        "outcome_validation_errors": note_label_audit.get("outcome_validation_errors", []),
        "outcome_validation_model": note_label_audit.get("outcome_validation_model", ""),
        "classification_status": note_label_audit.get("classification_status", "skipped"),
        "classification_failed_count": note_label_audit.get("classification_failed_count", 0),
    }
    missing_ids = list(note_label_audit.get("missing_ids") or [])
    missing_ids_detail = [
        {"id": item, "affected_input_dates": [row.target_date for row in historical_summaries if str(item).endswith(row.target_date)]}
        for item in missing_ids
    ]
    exclusion_reasons: list[str] = []
    if notes_payload["notes_parse_success_rate"] < MIN_NOTES_PARSE_RATE:
        exclusion_reasons.append("parse_success_rate_low")
    if notes_payload["unknown_rate"] > MAX_NOTES_UNKNOWN_RATE:
        exclusion_reasons.append("unknown_rate_high")
    if note_label_audit.get("date_match_failure_count", 0) > 0:
        exclusion_reasons.append("date_match_failure")
    if notes_date_merge_success_rate <= 0.0 and notes_non_empty_count > 0:
        exclusion_reasons.append("date_merge_failed_all")
    elif 0.0 < notes_date_merge_success_rate < 1.0:
        exclusion_reasons.append("date_merge_partial")
    if note_label_audit.get("duplicate_ids"):
        exclusion_reasons.append("duplicate_ids")
    if missing_ids:
        exclusion_reasons.append("missing_ids")
    if note_label_audit.get("unknown_ids"):
        exclusion_reasons.append("unknown_ids")
    quality_high_enough = (
        notes_payload["notes_parse_success_rate"] >= MIN_NOTES_PARSE_RATE
        and notes_date_merge_success_rate >= MIN_NOTES_DATE_MERGE_RATE
        and notes_payload["unknown_rate"] <= MAX_NOTES_UNKNOWN_RATE
    )
    if quality_high_enough:
        exclusion_reasons = [reason for reason in exclusion_reasons if reason != "missing_ids"]
    hard_exclusion_reasons = {"date_merge_failed_all", "parse_success_rate_low", "date_match_failure", "duplicate_ids", "unknown_ids"}
    notes_quality_used = "low" if any(reason in hard_exclusion_reasons for reason in exclusion_reasons) else "high"
    notes_payload["notes_quality_used"] = notes_quality_used
    notes_payload["exclusion_reason"] = exclusion_reasons
    notes_payload["missing_ids_detail"] = missing_ids_detail
    audit.put("notes_labeling", notes_payload)
    audit.info(
        "[TodayAdvice][Notes] total=%s non_empty=%s api_calls=%s labeled=%s fallback_unknown=%s",
        notes_payload["total_count"], notes_payload["non_empty_count"], notes_payload["api_calls"], notes_payload["labeled_count"], notes_payload["fallback_unknown_count"],
    )
    audit.info(
        "[TodayAdvice][Notes] raw_sentiment=%s normalized_sentiment=%s dataframe_sentiment=%s",
        safe_json(notes_payload["raw_sentiment_counts"]),
        safe_json(notes_payload["normalized_sentiment_counts"]),
        safe_json(notes_payload["dataframe_sentiment_counts"]),
    )
    audit.info(
        "[TodayAdvice][Notes] raw_flags=%s normalized_flags=%s dataframe_flags=%s",
        safe_json(notes_payload["raw_flag_counts"]),
        safe_json(notes_payload["normalized_flag_counts"]),
        safe_json(notes_payload["dataframe_flag_counts"]),
    )
    reason_counts = notes_payload["fallback_reason_counts"]
    audit.info(
        "[TodayAdvice][Notes] fallback_reasons: parse_error=%s schema_mismatch=%s date_match_failure=%s empty_response=%s",
        reason_counts.get("parse_error_count", 0),
        reason_counts.get("schema_mismatch_count", 0),
        reason_counts.get("date_match_failure_count", 0),
        reason_counts.get("empty_response_count", 0),
    )
    audit.info(
        "[TodayAdvice][Notes] classifier_success_rate=%s parse_success_rate=%s date_merge_success_rate=%s merge_failed_count=%s unknown_rate=%s tag_extract_failed=%s parse_low_confidence=%s",
        notes_payload["notes_classifier_success_rate"],
        notes_payload["notes_parse_success_rate"],
        notes_payload["notes_date_merge_success_rate"],
        notes_payload["notes_merge_failed_count"],
        notes_payload["unknown_rate"],
        notes_payload["tag_extract_failed_count"],
        notes_payload["parse_low_confidence_count"],
    )
    audit.info(
        "[Notes] notes_quality_used=%s exclusion_reason=%s missing_ids_detail=%s matched_dates_count=%s unmatched_input_dates=%s unmatched_response_dates=%s parse_success_rate=%s date_merge_success_rate=%s merge_failed_reason=%s unknown_rate=%s notes_based_features_included_count=%s notes_based_features_excluded_count=%s",
        notes_quality_used,
        safe_json(exclusion_reasons),
        safe_json(missing_ids_detail),
        notes_payload["matched_dates_count"],
        safe_json(notes_payload["unmatched_input_dates"]),
        safe_json(notes_payload["unmatched_response_dates"]),
        notes_payload["notes_parse_success_rate"],
        notes_payload["notes_date_merge_success_rate"],
        notes_payload["merge_failed_reason"],
        notes_payload["unknown_rate"],
        0 if notes_quality_used == "low" else len(note_labels),
        len(note_labels) if notes_quality_used == "low" else 0,
    )
    if notes_quality_used == "low":
        note_labels = {d: type(label)(**{**label.__dict__, "sentiment_label": "unknown", "sentiment_score": 0, "fatigue_flag": False, "stress_flag": False, "social_load_flag": False, "achievement_flag": False, "self_care_flag": False, "sleep_issue_flag": False, "signals": [], "derived_flags": {}, "confidence": "low", "parse_quality": "low", "no_signal_note": True, "tag_extract_failed": True, "parse_low_confidence": True}) for d, label in note_labels.items()}
        audit.info("[Notes] excluded_from_today_advice=true")
    elif "date_merge_partial" in exclusion_reasons:
        audit.info("[Notes] partial_merge_detected=true action=use_successful_labels_and_neutral_fallback")
    audit.info(
        "[TodayAdvice][Notes] top_tags=%s matched_dates_count=%s",
        safe_json(notes_payload["top_tags"]),
        notes_payload["matched_dates_count"],
    )
    audit.info(
        "[TodayAdvice][Notes] unmatched_input_dates=%s unmatched_response_dates=%s",
        safe_json(note_label_audit.get("unmatched_input_dates", [])),
        safe_json(note_label_audit.get("unmatched_response_dates", [])),
    )
    audit.info("[TodayAdvice][Notes] raw_response_paths=%s", safe_json(notes_payload["raw_response_paths"]))
    if notes_payload["raw_response_paths"]:
        audit.info("[TodayAdvice][Notes] raw_responses_saved=%s", safe_json(notes_payload["raw_response_paths"]))
    if notes_non_empty_count > 0 and notes_signals_detected_count == 0:
        logging.warning("notes_label_quality_warning target_date=%s reason=non_empty_but_no_signals", target_date)
    if notes_non_empty_count > 0 and notes_unknown_rate >= HIGH_UNKNOWN_WARNING_RATE:
        logging.warning("notes_label_quality_warning target_date=%s reason=unknown_rate_high unknown_rate=%.3f", target_date, notes_unknown_rate)
    if not any(notes_payload["dataframe_flag_counts"].values()):
        logging.warning("notes_label_quality_warning target_date=%s reason=dataframe_flags_all_zero", target_date)
    if not notes_payload["top_tags"]:
        logging.warning("notes_label_quality_warning target_date=%s reason=top_tags_empty", target_date)
    return NotesQualityResult(labels=note_labels, audit_metrics=notes_payload)
