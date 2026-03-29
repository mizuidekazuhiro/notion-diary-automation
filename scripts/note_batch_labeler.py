from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
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
    flags_payload = payload.get("flags") if isinstance(payload.get("flags"), Mapping) else {}
    if not signals_raw and isinstance(flags_payload, Mapping) and bool(flags_payload):
        mapped_tags: list[str] = []
        if flags_payload.get("fatigue"):
            mapped_tags.append("fatigue")
        if flags_payload.get("stress"):
            mapped_tags.append("stress")
        if flags_payload.get("social_load"):
            mapped_tags.append("social")
        if flags_payload.get("achievement"):
            mapped_tags.append("achievement")
        if flags_payload.get("self_care"):
            mapped_tags.append("recovery_action")
        if flags_payload.get("sleep_issue"):
            mapped_tags.append("sleep_issue")
        tags_raw = mapped_tags
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
    for signal_row in signals_raw:
        if not isinstance(signal_row, Mapping):
            continue
        tag = str(signal_row.get("tag") or "").strip()
        if tag not in ALLOWED_TAGS:
            continue
        conf = float(signal_row.get("confidence") or 0.0)
        conf = max(0.0, min(1.0, conf))
        confs.append(conf)
        tags.add(tag)
        signals.append({
            "tag": tag,
            "category": str(signal_row.get("category") or "unknown"),
            "polarity": str(signal_row.get("polarity") or "unknown"),
            "intensity": str(signal_row.get("intensity") or "unknown"),
            "confidence": conf,
            "evidence_text": str(signal_row.get("evidence_text") or ""),
        })
    for tag in allowed_tags:
        if tag in tags:
            continue
        tags.add(tag)
        confs.append(0.8)
        signals.append(_signal_from_tag(tag))

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
    if text.lower() in {"none", "null", "nan"}:
        return ""
    text = text.replace("/", "-")
    if "T" in text or text.endswith("Z"):
        iso_candidate = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(iso_candidate).date().isoformat()
        except ValueError:
            text = text.split("T", 1)[0]
    if len(text) >= 10:
        basic = text[:10]
        try:
            return datetime.strptime(basic, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return basic
    return text


def parse_note_label_json_with_meta(raw_text: str, input_rows: Sequence[Mapping[str, str]]) -> tuple[list[NoteLabel], dict[str, Any]]:
    fallback = {}
    row_key_to_date: dict[str, str] = {}
    row_id_to_key: dict[str, str] = {}
    date_to_keys: dict[str, list[str]] = {}
    input_ids: set[str] = set()
    for row in input_rows:
        row_id = str(row.get("id") or "").strip()
        raw_date = str(row.get("date"))
        normalized = _normalize_date_key(raw_date)
        key = row_id or normalized or raw_date
        fallback[key] = neutral_label(raw_date)
        row_key_to_date[key] = raw_date
        if row_id:
            row_id_to_key[row_id] = key
        normalized_date_key = normalized or raw_date
        date_to_keys.setdefault(normalized_date_key, []).append(key)
        if row_id:
            input_ids.add(row_id)
    meta: dict[str, Any] = {
        "parse_error": False,
        "empty_response": False,
        "matched_dates": set(),
        "matched_ids": set(),
        "schema_mismatch": False,
        "unmatched_response_dates": set(),
        "unmatched_input_dates": set(),
        "matched_dates_count": 0,
        "duplicate_ids": set(),
        "unknown_ids": set(),
        "missing_ids": set(),
        "input_count": len(input_rows),
        "output_count": 0,
        "input_dates_count": len(input_rows),
        "output_dates_count": 0,
        "missing_dates": set(),
        "duplicate_dates": set(),
        "merge_failed": False,
        "merge_failed_reason": None,
        "merge_key_mode_used": "id_then_date",
        "matched_by_id_count": 0,
        "matched_by_date_count": 0,
        "matched_by_order_count": 0,
        "date_only_match_count": 0,
        "id_date_conflict_count": 0,
        "order_merge_used": False,
        "unmatched_due_to_format_count": 0,
        "matched_input_keys": set(),
        "unmatched_input_keys": set(),
        "input_date_normalized_examples": [],
        "response_date_normalized_examples": [],
        "unmatched_input_dates": set(),
        "unmatched_response_dates": set(),
    }
    meta["input_date_normalized_examples"] = [
        {"raw": str(row.get("date") or ""), "normalized": _normalize_date_key(row.get("date"))}
        for row in list(input_rows)[:5]
    ]
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

    pending_for_order_merge: list[tuple[int, Mapping[str, Any]]] = []
    used_response_indexes: set[int] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        used_index = False
        row_id = str(row.get("id") or "").strip()
        raw_date = str(row.get("date") or "")
        date = _normalize_date_key(raw_date)
        key = row_id_to_key.get(row_id, row_id or date)
        if row_id:
            if row_id in meta["matched_ids"]:
                meta["duplicate_ids"].add(row_id)
                pending_for_order_merge.append((idx, row))
                continue
            if row_id not in input_ids:
                meta["unknown_ids"].add(row_id)
                pending_for_order_merge.append((idx, row))
                continue
            if row_id in row_id_to_key:
                key = row_id_to_key[row_id]
                meta["matched_by_id_count"] += 1
                used_index = True
            elif not date:
                meta["missing_dates"].add(raw_date)
                meta["merge_failed"] = True
                meta["merge_failed_reason"] = meta.get("merge_failed_reason") or "missing_date"
                pending_for_order_merge.append((idx, row))
                continue
        if key not in fallback:
            if not date:
                meta["missing_dates"].add(raw_date)
                meta["merge_failed"] = True
                meta["merge_failed_reason"] = meta.get("merge_failed_reason") or "missing_date"
                pending_for_order_merge.append((idx, row))
                continue
            candidate_keys = date_to_keys.get(date, [])
            if len(candidate_keys) == 1:
                key = candidate_keys[0]
                meta["matched_by_date_count"] += 1
                if not row_id:
                    meta["date_only_match_count"] += 1
                used_index = True
            elif len(candidate_keys) > 1:
                meta["duplicate_dates"].add(date)
                meta["merge_failed"] = True
                meta["merge_failed_reason"] = meta.get("merge_failed_reason") or "duplicate_date"
                pending_for_order_merge.append((idx, row))
                continue
            else:
                meta["unmatched_response_dates"].add(date)
                meta["unmatched_due_to_format_count"] += 1
                pending_for_order_merge.append((idx, row))
                continue
        canonical_date = row_key_to_date.get(key, date)
        canonical_norm = _normalize_date_key(canonical_date)
        if row_id and date and canonical_norm and date != canonical_norm:
            meta["id_date_conflict_count"] += 1
        normalized_row = row
        if not row_id and used_index:
            row_meta = dict(row.get("meta") or {})
            row_meta["parse_quality"] = "low"
            normalized_row = dict(row)
            normalized_row["meta"] = row_meta
        fallback[key] = _normalize_result(canonical_date, normalized_row)
        meta["matched_dates"].add(canonical_date)
        meta["matched_input_keys"].add(key)
        if row_id:
            meta["matched_ids"].add(row_id)
        if used_index:
            used_response_indexes.add(idx)
    can_order_merge = (
        not meta.get("parse_error")
        and not meta.get("schema_mismatch")
        and len(rows) == len(input_rows)
        and bool(pending_for_order_merge)
    )
    if can_order_merge:
        remaining_keys = [k for k in row_key_to_date.keys() if k not in meta["matched_input_keys"]]
        remaining_rows = [rows[idx] for idx in range(len(rows)) if idx not in used_response_indexes and isinstance(rows[idx], Mapping)]
        if len(remaining_keys) == len(remaining_rows):
            meta["order_merge_used"] = True
            for key, row in zip(remaining_keys, remaining_rows):
                row_meta = dict(row.get("meta") or {})
                row_meta["parse_quality"] = "low"
                normalized_row = dict(row)
                normalized_row["meta"] = row_meta
                canonical_date = row_key_to_date.get(key, str(row.get("date") or ""))
                fallback[key] = _normalize_result(canonical_date, normalized_row)
                meta["matched_dates"].add(canonical_date)
                meta["matched_input_keys"].add(key)
                meta["matched_by_order_count"] += 1
                order_row_id = str(row.get("id") or "").strip()
                if order_row_id and order_row_id in input_ids:
                    meta["matched_ids"].add(order_row_id)
    if input_ids:
        meta["missing_ids"] = set(input_ids) - set(meta["matched_ids"])
    meta["output_count"] = len(rows)
    meta["output_dates_count"] = len({str(_normalize_date_key(row.get("date"))) for row in rows if isinstance(row, Mapping)})
    parsed = []
    for row in input_rows:
        row_id = str(row.get("id") or "").strip()
        raw_date = str(row.get("date"))
        normalized = _normalize_date_key(raw_date)
        key = row_id or normalized or raw_date
        parsed.append(fallback[key])
    meta["matched_dates_count"] = len(set(meta.get("matched_dates") or set()))
    meta["unmatched_input_keys"] = set(row_key_to_date.keys()) - set(meta.get("matched_input_keys") or set())
    meta["unmatched_input_dates"] = set(row_key_to_date.values()) - set(meta.get("matched_dates") or set())
    input_date_set = set(row_key_to_date.values())
    output_date_set = {str(_normalize_date_key(row.get("date"))) for row in rows if isinstance(row, Mapping) and _normalize_date_key(row.get("date"))}
    meta["response_date_normalized_examples"] = [
        {"raw": str(row.get("date") or ""), "normalized": _normalize_date_key(row.get("date"))}
        for row in rows[:5]
        if isinstance(row, Mapping)
    ]
    missing_dates = sorted(input_date_set - output_date_set)
    meta["missing_dates"] = set(missing_dates)
    if len(rows) != len(input_rows):
        meta["merge_failed"] = True
        meta["merge_failed_reason"] = meta.get("merge_failed_reason") or "count_mismatch"
    if missing_dates:
        meta["merge_failed"] = True
        meta["merge_failed_reason"] = meta.get("merge_failed_reason") or "missing_dates"
    if output_date_set and output_date_set != set(_normalize_date_key(x) for x in input_date_set):
        meta["merge_failed"] = True
        meta["merge_failed_reason"] = meta.get("merge_failed_reason") or "normalization_mismatch"
    return parsed, meta


def _rule_based_note_label(row: Mapping[str, str]) -> NoteLabel:
    date = str(row.get("date") or "")
    text = str(row.get("notes") or "")
    patterns = {
        "fatigue": ["疲れた", "眠い", "だるい"],
        "stress": ["しんどい", "面倒", "後悔", "微妙", "イライラ"],
        "achievement": ["進んだ", "はかどった", "できた", "頑張れた"],
        "self_care": ["ジム", "休んだ", "早く寝た"],
        "sleep_issue": ["遅くまで起きた", "寝不足", "早く寝たかった"],
        "social_load": ["飲み会", "会食", "人と会って疲れた"],
    }
    tags: list[str] = []
    for tag, words in patterns.items():
        if any(word in text for word in words):
            tags.append(tag)
    if not tags:
        return neutral_label(date)
    payload = {"signals": [{"tag": tag, "category": "state", "polarity": "mixed", "intensity": "low", "confidence": 0.35, "evidence_text": ""} for tag in tags], "meta": {"parse_quality": "low"}}
    return _normalize_result(date, payload)


def _cache_dir() -> Path:
    return Path(os.getenv("NOTES_LABEL_CACHE_DIR", ".cache/notes_labels"))


def _cache_key(*, date: str, note_text: str, model: str) -> str:
    digest = sha256(f"{model}\n{date}\n{note_text}".encode("utf-8")).hexdigest()
    return f"{date}_{digest}.json"


def _load_cached_label(*, date: str, note_text: str, model: str) -> Optional[NoteLabel]:
    if os.getenv("NOTES_LABEL_CACHE_DISABLE", "").strip() == "1":
        return None
    if not note_text.strip():
        return neutral_label(date)
    path = _cache_dir() / _cache_key(date=date, note_text=note_text, model=model)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_result(date, payload)
    except Exception:
        return None


def _save_cached_label(*, date: str, note_text: str, model: str, label: NoteLabel) -> None:
    if os.getenv("NOTES_LABEL_CACHE_DISABLE", "").strip() == "1":
        return
    if not note_text.strip():
        return
    try:
        cache_dir = _cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "signals": label.signals,
            "meta": {"parse_quality": label.parse_quality},
            "derived_flags": label.derived_flags,
        }
        (cache_dir / _cache_key(date=date, note_text=note_text, model=model)).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        return


def _truncate_note(text: str) -> str:
    max_chars = int(os.getenv("NOTES_LABEL_MAX_CHARS", "1200") or "1200")
    trimmed = text.strip()
    return trimmed[:max_chars]


def label_notes_in_batches(summaries: Sequence[DailyLogSummary], *, chat_completion: Callable[..., str], model: str, batch_size: int = 8, raw_response_dir: str | None = None, audit: Optional[dict[str, Any]] = None) -> dict[str, NoteLabel]:
    rows = [{"id": f"note_{idx:04d}_{s.target_date}", "date": s.target_date, "notes": _truncate_note((s.notes or ""))} for idx, s in enumerate(summaries)]
    if not rows:
        return {}
    assets = load_notes_prompt_assets()
    debug_dir = Path(raw_response_dir) if raw_response_dir else None
    default_debug_dir = Path("debug/notes_labeler")
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
    duplicate_ids: set[str] = set()
    missing_ids: set[str] = set()
    unknown_ids: set[str] = set()
    missing_date_rows: set[str] = set()
    duplicate_date_rows: set[str] = set()
    merge_failed_reasons: Counter[str] = Counter()
    matched_by_id_count = 0
    matched_by_date_count = 0
    matched_by_order_count = 0
    order_merge_used = False
    date_only_match_count = 0
    id_date_conflict_count = 0
    unmatched_due_to_format_count = 0
    input_date_normalized_examples: list[dict[str, str]] = []
    response_date_normalized_examples: list[dict[str, str]] = []
    raw_sentiment_counts: Counter[str] = Counter()
    raw_flag_counts: Counter[str] = Counter()
    results: dict[str, NoteLabel] = {}
    cache_hit_count = 0
    fallback_covered_count = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        for row in [r for r in chunk if not r["notes"]]:
            results[row["date"]] = neutral_label(row["date"])
        targets: list[dict[str, str]] = []
        for row in [r for r in chunk if r["notes"]]:
            cached = _load_cached_label(date=row["date"], note_text=row["notes"], model=model)
            if cached is not None:
                results[row["date"]] = cached
                cache_hit_count += 1
            else:
                targets.append(row)
        if not targets:
            continue
        api_calls += 1
        user_payload = {
            "target_date": targets[-1]["date"],
            "rows": [{"id": r["id"], "date": r["date"], "text": r["notes"]} for r in targets],
            "constraints": [
                "top-level は配列または {\"rows\": [...]} のみ",
                "各rowは id/date/sentiment/flags/tags/confidence を必須で返す",
                "id は入力rowsのidをそのまま返す（欠損/改変禁止）",
                "date は入力rowsのdateをそのまま返す（欠損/改変禁止）",
                "返却順は入力順を維持し、欠損/重複/追加をしないこと",
                "曖昧なメモは tags=[] + flags全false + sentiment=unknown（またはneutral）",
            ],
            "few_shot": assets.get("few_shots", [])[:5],
            "schema": {
                "id": "input.id",
                "date": "input.date",
                "sentiment": "positive|neutral|negative|mixed|unknown",
                "flags": {
                    "fatigue": "boolean",
                    "stress": "boolean",
                    "social_load": "boolean",
                    "achievement": "boolean",
                    "self_care": "boolean",
                    "sleep_issue": "boolean",
                },
                "tags": ["string"],
                "confidence": "0.0-1.0",
            },
        }
        raw = ""
        final_parsed: list[NoteLabel] = []
        final_meta: dict[str, Any] = {}
        attempt_batch_sizes = [len(targets), max(1, len(targets) // 2)]

        def _record_metrics(meta: Mapping[str, Any], parsed_rows: Sequence[NoteLabel], expected_size: int) -> None:
            nonlocal parse_error_count, schema_mismatch_count, empty_response_count, date_match_failure_count
            parse_error_count += int(bool(meta.get("parse_error")))
            schema_mismatch_count += int(bool(meta.get("schema_mismatch")))
            empty_response_count += int(bool(meta.get("empty_response")))
            matched_dates.update(set(meta.get("matched_dates") or set()))
            unmatched_response_dates.update(set(meta.get("unmatched_response_dates") or set()))
            duplicate_ids.update(set(meta.get("duplicate_ids") or set()))
            missing_ids.update(set(meta.get("missing_ids") or set()))
            unknown_ids.update(set(meta.get("unknown_ids") or set()))
            missing_date_rows.update(set(meta.get("missing_dates") or set()))
            duplicate_date_rows.update(set(meta.get("duplicate_dates") or set()))
            if meta.get("merge_failed_reason"):
                merge_failed_reasons[str(meta.get("merge_failed_reason"))] += 1
            nonlocal matched_by_id_count, matched_by_date_count, unmatched_due_to_format_count
            nonlocal matched_by_order_count, date_only_match_count, order_merge_used, id_date_conflict_count
            matched_by_id_count += int(meta.get("matched_by_id_count", 0) or 0)
            matched_by_date_count += int(meta.get("matched_by_date_count", 0) or 0)
            matched_by_order_count += int(meta.get("matched_by_order_count", 0) or 0)
            date_only_match_count += int(meta.get("date_only_match_count", 0) or 0)
            id_date_conflict_count += int(meta.get("id_date_conflict_count", 0) or 0)
            order_merge_used = order_merge_used or bool(meta.get("order_merge_used"))
            unmatched_due_to_format_count += int(meta.get("unmatched_due_to_format_count", 0) or 0)
            if not input_date_normalized_examples and isinstance(meta.get("input_date_normalized_examples"), list):
                input_date_normalized_examples.extend(meta.get("input_date_normalized_examples")[:5])
            if isinstance(meta.get("response_date_normalized_examples"), list):
                response_date_normalized_examples.extend(meta.get("response_date_normalized_examples")[:5])
            if not meta.get("parse_error") and not meta.get("schema_mismatch"):
                date_match_failure_count += max(0, expected_size - len(set(meta.get("matched_dates") or set())))
            for row in parsed_rows:
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

        for attempt_index, _size in enumerate(attempt_batch_sizes):
            raw = chat_completion(model=model, system_prompt=assets["system_prompt"], user_prompt=json.dumps(user_payload, ensure_ascii=False))
            parsed, meta = parse_note_label_json_with_meta(raw, targets)
            _record_metrics(meta, parsed, len(targets))
            final_parsed = parsed
            final_meta = dict(meta)
            quality_ok = (
                not meta["parse_error"]
                and not meta["schema_mismatch"]
                and not meta.get("merge_failed")
                and not meta.get("duplicate_ids")
                and not meta.get("unknown_ids")
                and not meta.get("missing_ids")
                and int(meta.get("output_count", 0) or 0) == int(meta.get("input_count", 0) or 0)
            )
            if quality_ok:
                break
            if attempt_index == 0 and len(targets) > 1:
                half = max(1, len(targets) // 2)
                first, second = targets[:half], targets[half:]
                merged_partial: list[NoteLabel] = []
                partial_metas: list[dict[str, Any]] = []
                for sub in (first, second):
                    if not sub:
                        continue
                    partial_payload = {**user_payload, "rows": [{"id": r["id"], "date": r["date"], "text": r["notes"]} for r in sub]}
                    partial_raw = chat_completion(model=model, system_prompt=assets["system_prompt"], user_prompt=json.dumps(partial_payload, ensure_ascii=False))
                    partial_parsed, partial_meta = parse_note_label_json_with_meta(partial_raw, sub)
                    _record_metrics(partial_meta, partial_parsed, len(sub))
                    merged_partial.extend(partial_parsed)
                    partial_metas.append(partial_meta)
                if merged_partial:
                    final_parsed = merged_partial
                    final_meta = {
                        "parse_error": any(m.get("parse_error") for m in partial_metas),
                        "schema_mismatch": any(m.get("schema_mismatch") for m in partial_metas),
                        "merge_failed": any(m.get("merge_failed") for m in partial_metas),
                    }
                break
        parsed = final_parsed
        should_force_debug_save = bool(
            final_meta.get("merge_failed")
            or final_meta.get("unmatched_input_dates")
            or final_meta.get("unknown_ids")
            or final_meta.get("missing_ids")
            or final_meta.get("duplicate_ids")
            or final_meta.get("order_merge_used")
        )
        write_debug_dir = debug_dir
        if should_force_debug_save and write_debug_dir is None:
            write_debug_dir = default_debug_dir
        if write_debug_dir:
            try:
                write_debug_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                path = write_debug_dir / f"notes_batch_{i // batch_size:02d}_{ts}.json"
                path.write_text(str(raw), encoding="utf-8")
                raw_response_paths.append(str(path))
            except Exception as exc:  # noqa: BLE001
                logging.warning("notes_label_raw_save_failed batch=%s error=%s", i // batch_size, exc)
        for item in parsed:
            results[item.date] = item
            source_row = next((r for r in targets if r["date"] == item.date), None)
            if source_row is not None:
                _save_cached_label(date=item.date, note_text=source_row["notes"], model=model, label=item)
        for row in targets:
            key = str(row["id"])
            unmatched_keys = set(final_meta.get("unmatched_input_keys") or set())
            if row["date"] not in results or key in unmatched_keys:
                results[row["date"]] = _rule_based_note_label(row)
                fallback_covered_count += 1

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
            "missing_date_count": len(missing_date_rows),
            "duplicate_date_count": len(duplicate_date_rows),
            "unknown_ids_count": len(unknown_ids),
            "missing_ids_count": len(missing_ids),
        }
        final_coverage_count = len([d for d in all_input_dates if d in ordered and isinstance(ordered[d], NoteLabel)])
        final_coverage_rate = round(final_coverage_count / len(all_input_dates), 3) if all_input_dates else 0.0
        labels_usable = final_coverage_count == len(all_input_dates)
        merge_quality_low = bool(
            unmatched_input_dates
            or unmatched_response_dates
            or duplicate_ids
            or missing_ids
            or unknown_ids
            or date_match_failure_count
            or unmatched_due_to_format_count
            or order_merge_used
            or date_only_match_count
            or id_date_conflict_count
        )
        fatal_reason: str | None = None
        if parse_error_count > 0:
            fatal_reason = "parse_error"
        elif schema_mismatch_count > 0:
            fatal_reason = "schema_mismatch"
        elif empty_response_count > 0:
            fatal_reason = "empty_response"
        elif not labels_usable:
            fatal_reason = "insufficient_coverage"
        labeling_failed = bool(fatal_reason)
        audit.update({
            "api_calls": api_calls,
            "notes_classifier_success_rate": round(parse_success / len(labels), 3) if labels else 0.0,
            "notes_parse_success_rate": round(1.0 - ((parse_error_count + schema_mismatch_count + empty_response_count) / max(api_calls, 1)), 3) if labels else 0.0,
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
            "notes_date_merge_success_rate": round(len(matched_dates) / len(all_input_dates), 3) if all_input_dates else 0.0,
            "date_merge_success_rate": round(len(matched_dates) / len(all_input_dates), 3) if all_input_dates else 0.0,
            "notes_merge_failed_count": int(date_match_failure_count + len(missing_ids) + len(unknown_ids) + len(duplicate_ids)),
            "merge_failed_count": int(date_match_failure_count + len(missing_ids) + len(unknown_ids) + len(duplicate_ids)),
            "notes_unmatched_input_dates_count": len(unmatched_input_dates),
            "notes_unmatched_response_dates_count": len(unmatched_response_dates),
            "input_dates_count": len(all_input_dates),
            "output_dates_count": len(matched_dates) + len(unmatched_response_dates),
            "matched_dates": sorted(matched_dates),
            "unmatched_input_dates": unmatched_input_dates,
            "unmatched_response_dates": sorted(unmatched_response_dates),
            "missing_dates": unmatched_input_dates,
            "duplicate_dates": sorted(duplicate_date_rows),
            "merge_failed_reason": ",".join(sorted(merge_failed_reasons.keys())) if merge_failed_reasons else ("merge_failed" if (date_match_failure_count or missing_ids or unknown_ids or duplicate_ids or unmatched_input_dates or unmatched_response_dates) else None),
            "merge_failed_reason_counts": dict(merge_failed_reasons),
            "parse_error": bool(parse_error_count),
            "schema_mismatch": bool(schema_mismatch_count),
            "missing_date": sorted(missing_date_rows),
            "duplicate_date": sorted(duplicate_date_rows),
            "duplicate_ids": sorted(duplicate_ids),
            "missing_ids": sorted(missing_ids),
            "unknown_ids": sorted(unknown_ids),
            "duplicate_ids_count": len(duplicate_ids),
            "missing_ids_count": len(missing_ids),
            "unknown_ids_count": len(unknown_ids),
            "merge_key_mode_used": "id_then_date",
            "matched_by_id_count": matched_by_id_count,
            "matched_by_date_count": matched_by_date_count,
            "matched_by_order_count": matched_by_order_count,
            "order_merge_used": order_merge_used,
            "date_only_match_count": date_only_match_count,
            "id_date_conflict_count": id_date_conflict_count,
            "unmatched_due_to_format_count": unmatched_due_to_format_count,
            "input_date_normalized_examples": input_date_normalized_examples[:5],
            "response_date_normalized_examples": response_date_normalized_examples[:5],
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
            "cache_hit_count": cache_hit_count,
            "cache_miss_count": max(0, len([r for r in rows if r.get("notes")]) - cache_hit_count),
            "fallback_covered_count": fallback_covered_count,
            "final_coverage_count": final_coverage_count,
            "final_coverage_rate": final_coverage_rate,
            "labels_usable": labels_usable,
            "merge_quality_low": merge_quality_low,
            "labeling_failed_fatal_reason": fatal_reason,
            "labeling_failed": labeling_failed,
        })
        logging.info(
            "notes_labeling_audit_summary labeling_failed_fatal_reason=%s parse_error_count=%s schema_mismatch_count=%s empty_response_count=%s final_coverage_rate=%s matched_by_id_count=%s matched_by_date_count=%s matched_by_order_count=%s missing_ids_count=%s unknown_ids_count=%s duplicate_ids_count=%s unmatched_input_dates_count=%s unmatched_response_dates_count=%s merge_failed_reason=%s",
            fatal_reason,
            parse_error_count,
            schema_mismatch_count,
            empty_response_count,
            final_coverage_rate,
            matched_by_id_count,
            matched_by_date_count,
            matched_by_order_count,
            len(missing_ids),
            len(unknown_ids),
            len(duplicate_ids),
            len(unmatched_input_dates),
            len(unmatched_response_dates),
            audit.get("merge_failed_reason"),
        )
        if audit.get("notes_date_merge_success_rate", 0.0) <= 0.0 and all_input_dates:
            if unknown_ids or missing_ids:
                audit["exclusion_reason"] = "id_merge_failed_all"
            elif unmatched_input_dates:
                audit["exclusion_reason"] = "date_merge_failed_all"
            else:
                audit["exclusion_reason"] = "normalization_mismatch_all"
    logging.info("notes batch label count=%s", len(rows))
    return ordered
