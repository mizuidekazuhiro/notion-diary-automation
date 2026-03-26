from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
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
    signals_raw = payload.get("signals") if isinstance(payload.get("signals"), list) else []
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


def parse_note_label_json_with_meta(raw_text: str, input_rows: Sequence[Mapping[str, str]]) -> tuple[list[NoteLabel], dict[str, Any]]:
    fallback = {str(r.get("date")): neutral_label(str(r.get("date"))) for r in input_rows}
    meta: dict[str, Any] = {"parse_error": False, "empty_response": False, "matched_dates": set(), "schema_mismatch": False}
    if not str(raw_text or "").strip():
        meta["empty_response"] = True
        return list(fallback.values()), meta
    try:
        raw = json.loads(raw_text)
    except Exception:
        meta["parse_error"] = True
        return list(fallback.values()), meta
    if not isinstance(raw, list):
        meta["schema_mismatch"] = True
        return list(fallback.values()), meta
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        date = str(row.get("date") or "")
        if date not in fallback:
            continue
        fallback[date] = _normalize_result(date, row)
        meta["matched_dates"].add(date)
    return [fallback[str(r.get("date"))] for r in input_rows], meta


def label_notes_in_batches(*, summaries: Sequence[DailyLogSummary], chat_completion: Callable[..., str], model: str, batch_size: int = 15, raw_response_dir: str | None = None, audit: Optional[dict[str, Any]] = None) -> dict[str, NoteLabel]:
    rows = [{"date": s.target_date, "notes": (s.notes or "").strip()} for s in summaries]
    if not rows:
        return {}
    assets = load_notes_prompt_assets()
    debug_dir = Path(raw_response_dir) if raw_response_dir else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)

    api_calls = 0
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
            if not meta["parse_error"] and not meta["schema_mismatch"]:
                break
        parsed, _ = parse_note_label_json_with_meta(raw, targets)
        if debug_dir:
            (debug_dir / f"notes_batch_{i // batch_size:02d}.json").write_text(str(raw), encoding="utf-8")
        for item in parsed:
            results[item.date] = item

    ordered = {row["date"]: results.get(row["date"], neutral_label(row["date"])) for row in rows}
    if audit is not None:
        labels = list(ordered.values())
        all_tags = [sig.get("tag") for x in labels for sig in x.signals if sig.get("tag")]
        parse_success = sum(1 for x in labels if not x.tag_extract_failed)
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
        })
    logging.info("notes batch label count=%s", len(rows))
    return ordered
