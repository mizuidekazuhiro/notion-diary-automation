from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from collections import Counter
from typing import Any, Callable, Mapping, Optional, Sequence

from publish.read_daily_log import DailyLogSummary
from scripts.notes_prompt_assets import load_notes_prompt_assets

ALLOWED_TAGS = {
    "exercise", "gym", "social", "drinking", "work_focus", "work_progress", "late_work", "early_home", "commute_train", "taxi_use", "shopping_small", "overeating", "diet_control", "meal_disruption", "sleep_late", "early_wake", "recovery_action", "presentation_work", "dc_work", "business_trip",
    "fatigue", "stress", "conflict", "regret", "stable", "productive", "moderate_productivity", "distracted", "low_motivation", "achievement", "money_saved", "spending_pressure", "sleep_issue",
    "family_event", "interview_memo", "admin_task", "health_management",
}


@dataclass(frozen=True)
class NoteLabel:
    date: str
    sentiment_label: str
    sentiment_score: int
    fatigue_flag: bool
    stress_flag: bool
    social_load_flag: bool
    achievement_flag: bool
    self_care_flag: bool
    sleep_issue_flag: bool
    confidence: str
    evidence_keywords: list[str]
    signals: list[dict[str, Any]] = field(default_factory=list)
    derived_flags: dict[str, bool] = field(default_factory=dict)
    parse_quality: str = "low"
    no_signal_note: bool = True
    tag_extract_failed: bool = False
    parse_low_confidence: bool = True


def neutral_label(date: str) -> NoteLabel:
    return NoteLabel(date, "unknown", 0, False, False, False, False, False, False, "low", [], [], {}, "low", True, True, True)


def _confidence_band(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _normalize_result(date: str, payload: Mapping[str, Any]) -> NoteLabel:
    def _signal_from_tag(tag: str) -> dict[str, Any]:
        negative_state = {"fatigue", "stress", "conflict", "regret", "sleep_issue"}
        positive_state = {"productive", "moderate_productivity", "achievement", "money_saved"}
        behavior_tags = {"social", "drinking", "exercise", "gym", "late_work", "early_home", "business_trip", "presentation_work", "dc_work"}
        category = "context"
        polarity = "unknown"
        if tag in negative_state:
            category, polarity = "state", "negative"
        elif tag in positive_state:
            category, polarity = "state", "positive"
        elif tag in behavior_tags:
            category, polarity = "behavior", "mixed"
        return {
            "tag": tag,
            "category": category,
            "polarity": polarity,
            "intensity": "medium",
            "confidence": 0.8,
            "evidence_text": "",
        }

    signals_raw = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    tags_raw = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    allowed_tags = [str(tag).strip() for tag in tags_raw if str(tag).strip() in ALLOWED_TAGS]
    if not signals_raw and allowed_tags:
        signals_raw = [_signal_from_tag(tag) for tag in allowed_tags]
    if not signals_raw and any(k in payload for k in ["fatigue_flag", "stress_flag", "sleep_issue_flag", "achievement_flag", "social_load_flag"]):
        legacy = []
        if payload.get("fatigue_flag"):
            legacy.append({"tag": "fatigue", "category": "state", "polarity": "negative", "intensity": "medium", "confidence": 0.8, "evidence_text": ""})
        if payload.get("stress_flag"):
            legacy.append({"tag": "stress", "category": "state", "polarity": "negative", "intensity": "medium", "confidence": 0.8, "evidence_text": ""})
        if payload.get("sleep_issue_flag"):
            legacy.append({"tag": "sleep_issue", "category": "state", "polarity": "negative", "intensity": "medium", "confidence": 0.8, "evidence_text": ""})
        if payload.get("achievement_flag"):
            legacy.append({"tag": "achievement", "category": "state", "polarity": "positive", "intensity": "medium", "confidence": 0.8, "evidence_text": ""})
        if payload.get("social_load_flag"):
            legacy.append({"tag": "social", "category": "behavior", "polarity": "mixed", "intensity": "medium", "confidence": 0.7, "evidence_text": ""})
        signals_raw = legacy
    signals: list[dict[str, Any]] = []
    tags: set[str] = set()
    confs: list[float] = []
    for row in signals_raw:
        if not isinstance(row, Mapping):
            continue
        tag = str(row.get("tag") or "").strip()
        if tag not in ALLOWED_TAGS:
            continue
        conf = float(row.get("confidence") or 0.0)
        conf = max(0.0, min(1.0, conf))
        confs.append(conf)
        tags.add(tag)
        signals.append({
            "tag": tag,
            "category": str(row.get("category") or "unknown"),
            "polarity": str(row.get("polarity") or "unknown"),
            "intensity": str(row.get("intensity") or "unknown"),
            "confidence": conf,
            "evidence_text": str(row.get("evidence_text") or ""),
        })

    derived = dict(payload.get("derived_flags") or {})
    if "gym" in tags and "exercise" not in tags:
        tags.add("exercise")
        derived["exercise"] = True
    derived["social_load_flag"] = bool(("social" in tags) and ("drinking" in tags))
    derived["recovery_like_flag"] = bool(("early_home" in tags) and ("exercise" in tags or "gym" in tags))
    derived["self_control_flag"] = bool(("commute_train" in tags) and ("money_saved" in tags))
    derived["work_progress_flag"] = bool(("moderate_productivity" in tags) and ("work_progress" in tags))
    derived["life_disruption_flag"] = bool("conflict" in tags or "regret" in tags)

    avg_conf = sum(confs) / len(confs) if confs else 0.0
    confidence = _confidence_band(avg_conf)
    parse_quality = str((payload.get("meta") or {}).get("parse_quality") or "low")
    if parse_quality not in {"high", "medium", "low"}:
        parse_quality = "low"

    sentiment_label = "unknown"
    sentiment_score = 0
    if "productive" in tags or "achievement" in tags:
        sentiment_label = "positive"
        sentiment_score = 1
    elif {"stress", "fatigue", "conflict", "regret", "sleep_issue"} & tags:
        sentiment_label = "negative"
        sentiment_score = -1

    return NoteLabel(
        date=date,
        sentiment_label=sentiment_label,
        sentiment_score=sentiment_score,
        fatigue_flag="fatigue" in tags,
        stress_flag="stress" in tags,
        social_load_flag=bool(derived.get("social_load_flag")),
        achievement_flag="achievement" in tags,
        self_care_flag=bool(derived.get("recovery_like_flag")),
        sleep_issue_flag="sleep_issue" in tags,
        confidence=confidence,
        evidence_keywords=[s["evidence_text"] for s in signals if s.get("evidence_text")][:6],
        signals=signals,
        derived_flags={k: bool(v) for k, v in derived.items()},
        parse_quality=parse_quality,
        no_signal_note=len(tags) == 0,
        tag_extract_failed=len(tags) == 0,
        parse_low_confidence=(parse_quality == "low" or confidence == "low"),
    )


def parse_note_label_json(raw_text: str, input_rows: Sequence[Mapping[str, str]]) -> list[NoteLabel]:
    parsed, _ = parse_note_label_json_with_meta(raw_text, input_rows)
    return parsed


def _normalize_date_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("/", "-")
    if "T" in text:
        text = text.split("T", 1)[0]
    return text


def parse_note_label_json_with_meta(raw_text: str, input_rows: Sequence[Mapping[str, str]]) -> tuple[list[NoteLabel], dict[str, Any]]:
    fallback = {}
    input_by_date: dict[str, str] = {}
    for row in input_rows:
        raw_date = str(row.get("date"))
        normalized = _normalize_date_key(raw_date)
        fallback[normalized or raw_date] = neutral_label(raw_date)
        input_by_date[normalized or raw_date] = raw_date
    meta: dict[str, Any] = {"parse_error": False, "empty_response": False, "matched_dates": set(), "schema_mismatch": False, "unmatched_response_dates": set()}
    if not str(raw_text or "").strip():
        meta["empty_response"] = True
        return list(fallback.values()), meta
    try:
        raw = json.loads(raw_text)
    except Exception:
        meta["parse_error"] = True
        return list(fallback.values()), meta
    rows: list[Any] = []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, Mapping) and isinstance(raw.get("rows"), list):
        rows = list(raw.get("rows") or [])
    elif isinstance(raw, Mapping) and isinstance(raw.get("results"), list):
        rows = list(raw.get("results") or [])
    else:
        meta["schema_mismatch"] = True
        return list(fallback.values()), meta

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        raw_date = str(row.get("date") or "")
        date = _normalize_date_key(raw_date)
        if date not in fallback:
            if date:
                meta["unmatched_response_dates"].add(date)
            continue
        canonical_date = input_by_date.get(date, date)
        fallback[date] = _normalize_result(canonical_date, row)
        meta["matched_dates"].add(canonical_date)
    parsed = []
    for row in input_rows:
        raw_date = str(row.get("date"))
        normalized = _normalize_date_key(raw_date) or raw_date
        parsed.append(fallback[normalized])
    return parsed, meta


def label_notes_in_batches(*, summaries: Sequence[DailyLogSummary], chat_completion: Callable[..., str], model: str, batch_size: int = 15, raw_response_dir: str | None = None, audit: Optional[dict[str, Any]] = None) -> dict[str, NoteLabel]:
    rows = [{"date": s.target_date, "notes": (s.notes or "").strip()} for s in summaries]
    if not rows:
        return {}
    assets = load_notes_prompt_assets()
    debug_dir = Path(raw_response_dir) if raw_response_dir else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)

    api_calls = 0
    parse_error_count = 0
    schema_mismatch_count = 0
    date_match_failure_count = 0
    empty_response_count = 0
    raw_response_paths: list[str] = []
    matched_dates: set[str] = set()
    unmatched_response_dates: set[str] = set()
    raw_sentiment_counts: Counter[str] = Counter()
    raw_flag_counts: Counter[str] = Counter()
    results: dict[str, NoteLabel] = {}
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        for row in [r for r in chunk if not r["notes"]]:
            results[row["date"]] = neutral_label(row["date"])
        targets = [r for r in chunk if r["notes"]]
        if not targets:
            continue
        api_calls += 1
        user_payload = {
            "target_date": targets[-1]["date"],
            "rows": targets,
            "few_shot": assets.get("few_shots", [])[:5],
            "schema": {
                "date": "YYYY-MM-DD",
                "summary_short_ja": "str",
                "signals": [{"tag": "enum", "category": "behavior|state|context", "polarity": "positive|negative|neutral|mixed|unknown", "intensity": "low|medium|high|unknown", "confidence": "0-1", "evidence_text": "quoted text"}],
                "derived_flags": "object",
                "meta": {"parse_quality": "high|medium|low", "has_clear_signal": "bool", "unknown_reason": "str|null"},
            },
        }
        raw = ""
        for _ in range(2):
            raw = chat_completion(model=model, system_prompt=assets["system_prompt"], user_prompt=json.dumps(user_payload, ensure_ascii=False))
            parsed, meta = parse_note_label_json_with_meta(raw, targets)
            parse_error_count += int(bool(meta.get("parse_error")))
            schema_mismatch_count += int(bool(meta.get("schema_mismatch")))
            empty_response_count += int(bool(meta.get("empty_response")))
            matched_dates.update(set(meta.get("matched_dates") or set()))
            unmatched_response_dates.update(set(meta.get("unmatched_response_dates") or set()))
            if not meta["parse_error"] and not meta["schema_mismatch"]:
                date_match_failure_count += max(0, len(targets) - len(set(meta.get("matched_dates") or set())))
            for row in parsed:
                raw_sentiment_counts[row.sentiment_label] += 1
                if row.fatigue_flag:
                    raw_flag_counts["fatigue"] += 1
                if row.stress_flag:
                    raw_flag_counts["stress"] += 1
                if row.social_load_flag:
                    raw_flag_counts["social_load"] += 1
                if row.achievement_flag:
                    raw_flag_counts["achievement"] += 1
                if row.self_care_flag:
                    raw_flag_counts["self_care"] += 1
                if row.sleep_issue_flag:
                    raw_flag_counts["sleep_issue"] += 1
            if not meta["parse_error"] and not meta["schema_mismatch"]:
                break
        parsed, _ = parse_note_label_json_with_meta(raw, targets)
        if debug_dir:
            path = debug_dir / f"notes_batch_{i // batch_size:02d}.json"
            path.write_text(str(raw), encoding="utf-8")
            raw_response_paths.append(str(path))
        for item in parsed:
            results[item.date] = item

    ordered = {row["date"]: results.get(row["date"], neutral_label(row["date"])) for row in rows}
    if audit is not None:
        all_input_dates = [str(r["date"]) for r in rows]
        unmatched_input_dates = sorted(set(all_input_dates) - matched_dates)
        labels = list(ordered.values())
        all_tags = [sig.get("tag") for x in labels for sig in x.signals if sig.get("tag")]
        parse_success = sum(1 for x in labels if not x.tag_extract_failed)
        normalized_sentiment_counts = Counter(x.sentiment_label for x in labels)
        normalized_flag_counts: Counter[str] = Counter()
        for x in labels:
            if x.fatigue_flag:
                normalized_flag_counts["fatigue"] += 1
            if x.stress_flag:
                normalized_flag_counts["stress"] += 1
            if x.social_load_flag:
                normalized_flag_counts["social_load"] += 1
            if x.achievement_flag:
                normalized_flag_counts["achievement"] += 1
            if x.self_care_flag:
                normalized_flag_counts["self_care"] += 1
            if x.sleep_issue_flag:
                normalized_flag_counts["sleep_issue"] += 1
        fallback_reason_counts = {
            "parse_error_count": parse_error_count,
            "schema_mismatch_count": schema_mismatch_count,
            "date_match_failure_count": date_match_failure_count,
            "empty_response_count": empty_response_count,
        }
        audit.update({
            "api_calls": api_calls,
            "notes_classifier_success_rate": round(parse_success / len(labels), 3) if labels else 0.0,
            "notes_parse_success_rate": round(parse_success / len(labels), 3) if labels else 0.0,
            "notes_unknown_rate": round(sum(1 for x in labels if x.sentiment_label == "unknown") / len(labels), 3) if labels else 0.0,
            "notes_low_confidence_rate": round(sum(1 for x in labels if x.parse_low_confidence) / len(labels), 3) if labels else 0.0,
            "notes_extracted_tags_count": len(all_tags),
            "notes_top_tags": sorted({t: all_tags.count(t) for t in set(all_tags)}.items(), key=lambda p: p[1], reverse=True)[:10],
            "notes_avg_confidence": round(sum({"low": 0.3, "medium": 0.6, "high": 0.9}.get(x.confidence, 0.0) for x in labels) / len(labels), 3) if labels else 0.0,
            "notes_parse_quality_distribution": {k: sum(1 for x in labels if x.parse_quality == k) for k in ["high", "medium", "low"]},
            "parse_error_count": parse_error_count,
            "schema_mismatch_count": schema_mismatch_count,
            "date_match_failure_count": date_match_failure_count,
            "empty_response_count": empty_response_count,
            "raw_response_paths": raw_response_paths,
            "matched_dates_count": len(matched_dates),
            "matched_dates": sorted(matched_dates),
            "unmatched_input_dates": unmatched_input_dates,
            "unmatched_response_dates": sorted(unmatched_response_dates),
            "tags_detected_count": len(all_tags),
            "signals_detected_count": len(all_tags),
            "extracted_tag_count": len(all_tags),
            "extracted_signal_count": len(all_tags),
            "unknown_count": sum(1 for x in labels if x.sentiment_label == "unknown"),
            "unknown_rate": round(sum(1 for x in labels if x.sentiment_label == "unknown") / len(labels), 3) if labels else 0.0,
            "tag_extract_failed_count": sum(1 for x in labels if x.tag_extract_failed),
            "parse_low_confidence_count": sum(1 for x in labels if x.parse_low_confidence),
            "top_tags": sorted({t: all_tags.count(t) for t in set(all_tags)}.items(), key=lambda p: p[1], reverse=True)[:10],
            "raw_sentiment_counts": dict(raw_sentiment_counts),
            "normalized_sentiment_counts": dict(normalized_sentiment_counts),
            "raw_flag_counts": dict(raw_flag_counts),
            "normalized_flag_counts": dict(normalized_flag_counts),
            "fallback_reason_counts": fallback_reason_counts,
        })
    logging.info("notes batch label count=%s", len(rows))
    return ordered
