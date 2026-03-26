from __future__ import annotations

import json
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from publish.read_daily_log import DailyLogSummary


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "sentiment_label": self.sentiment_label,
            "sentiment_score": self.sentiment_score,
            "fatigue_flag": self.fatigue_flag,
            "stress_flag": self.stress_flag,
            "social_load_flag": self.social_load_flag,
            "achievement_flag": self.achievement_flag,
            "self_care_flag": self.self_care_flag,
            "sleep_issue_flag": self.sleep_issue_flag,
            "confidence": self.confidence,
            "evidence_keywords": self.evidence_keywords,
        }


def neutral_label(date: str) -> NoteLabel:
    return NoteLabel(
        date=date,
        sentiment_label="neutral",
        sentiment_score=0,
        fatigue_flag=False,
        stress_flag=False,
        social_load_flag=False,
        achievement_flag=False,
        self_care_flag=False,
        sleep_issue_flag=False,
        confidence="low",
        evidence_keywords=[],
    )


def _normalize_item(item: Mapping[str, Any], date: str) -> NoteLabel:
    base = neutral_label(date).to_dict()
    payload = {**base, **dict(item), "date": date}
    sentiment = str(payload.get("sentiment_label") or "neutral").strip().lower()
    if sentiment not in {"positive", "neutral", "negative"}:
        sentiment = "neutral"
    confidence = str(payload.get("confidence") or "low").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    try:
        score = int(payload.get("sentiment_score", 0))
    except Exception:
        score = 0
    score = max(-2, min(2, score))
    keywords = payload.get("evidence_keywords")
    if not isinstance(keywords, list):
        keywords = []
    return NoteLabel(
        date=date,
        sentiment_label=sentiment,
        sentiment_score=score,
        fatigue_flag=bool(payload.get("fatigue_flag", False)),
        stress_flag=bool(payload.get("stress_flag", False)),
        social_load_flag=bool(payload.get("social_load_flag", False)),
        achievement_flag=bool(payload.get("achievement_flag", False)),
        self_care_flag=bool(payload.get("self_care_flag", False)),
        sleep_issue_flag=bool(payload.get("sleep_issue_flag", False)),
        confidence=confidence,
        evidence_keywords=[str(k) for k in keywords if str(k).strip()][:5],
    )


def parse_note_label_json(raw_text: str, input_rows: Sequence[Mapping[str, str]]) -> list[NoteLabel]:
    parsed, _meta = parse_note_label_json_with_meta(raw_text, input_rows)
    return parsed


def parse_note_label_json_with_meta(raw_text: str, input_rows: Sequence[Mapping[str, str]]) -> tuple[list[NoteLabel], dict[str, Any]]:
    fallback = {str(row.get("date")): neutral_label(str(row.get("date"))) for row in input_rows}
    matched_dates: set[str] = set()
    meta: dict[str, Any] = {
        "empty_response": False,
        "parse_error": False,
        "schema_mismatch": False,
        "matched_dates": matched_dates,
    }
    if not raw_text or not str(raw_text).strip():
        meta["empty_response"] = True
        return list(fallback.values()), meta
    try:
        parsed = json.loads(raw_text)
    except Exception:
        meta["parse_error"] = True
        return list(fallback.values()), meta
    if not isinstance(parsed, list):
        meta["schema_mismatch"] = True
        return list(fallback.values()), meta
    for row in parsed:
        if not isinstance(row, Mapping):
            continue
        date = str(row.get("date") or "").strip()
        if date not in fallback:
            continue
        fallback[date] = _normalize_item(row, date)
        matched_dates.add(date)
    return [fallback[str(row.get("date"))] for row in input_rows], meta


def label_notes_in_batches(
    *,
    summaries: Sequence[DailyLogSummary],
    chat_completion: Callable[..., str],
    model: str,
    batch_size: int = 15,
    raw_response_dir: str | None = None,
    audit: Optional[dict[str, Any]] = None,
) -> dict[str, NoteLabel]:
    rows = [{"date": s.target_date, "notes": (s.notes or "").strip()} for s in summaries]
    if not rows:
        return {}
    api_calls = 0
    results: dict[str, NoteLabel] = {}
    reason_counts = {
        "parse_error_count": 0,
        "schema_mismatch_count": 0,
        "date_match_failure_count": 0,
        "empty_response_count": 0,
    }
    raw_response_paths: list[str] = []
    raw_sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0, "unknown": 0}
    raw_flag_counts = {"fatigue": 0, "stress": 0, "social_load": 0, "achievement": 0, "self_care": 0, "sleep_issue": 0}
    normalized_sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
    normalized_flag_counts = {"fatigue": 0, "stress": 0, "social_load": 0, "achievement": 0, "self_care": 0, "sleep_issue": 0}
    debug_dir = Path(raw_response_dir) if raw_response_dir else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
    for index in range(0, len(rows), batch_size):
        chunk = rows[index : index + batch_size]
        missing = [r for r in chunk if not r["notes"]]
        for row in missing:
            results[row["date"]] = neutral_label(row["date"])
        targets = [r for r in chunk if r["notes"]]
        if not targets:
            continue
        api_calls += 1
        try:
            prompt = (
                "次の配列を日付ごとにラベル化し、JSON配列のみを返してください。"
                "空文字はneutral score0 flags false confidence low。\n"
                f"input={json.dumps(targets, ensure_ascii=False)}"
            )
            raw = chat_completion(
                model=model,
                system_prompt="あなたは日本語Notesを構造化ラベル化する。出力はJSONのみ。",
                user_prompt=prompt,
            )
            try:
                raw_items = json.loads(str(raw))
                if isinstance(raw_items, list):
                    for raw_item in raw_items:
                        if not isinstance(raw_item, Mapping):
                            continue
                        sentiment = str(raw_item.get("sentiment_label") or "").strip().lower()
                        if sentiment in raw_sentiment_counts:
                            raw_sentiment_counts[sentiment] += 1
                        else:
                            raw_sentiment_counts["unknown"] += 1
                        raw_flag_counts["fatigue"] += int(bool(raw_item.get("fatigue_flag")))
                        raw_flag_counts["stress"] += int(bool(raw_item.get("stress_flag")))
                        raw_flag_counts["social_load"] += int(bool(raw_item.get("social_load_flag")))
                        raw_flag_counts["achievement"] += int(bool(raw_item.get("achievement_flag")))
                        raw_flag_counts["self_care"] += int(bool(raw_item.get("self_care_flag")))
                        raw_flag_counts["sleep_issue"] += int(bool(raw_item.get("sleep_issue_flag")))
            except Exception:
                pass
            if debug_dir:
                first_date = targets[0]["date"] if targets else "na"
                file_path = debug_dir / f"notes_batch_{index // batch_size:02d}_{first_date}.txt"
                file_path.write_text(str(raw), encoding="utf-8")
                raw_response_paths.append(str(file_path))
            parsed, meta = parse_note_label_json_with_meta(raw, targets)
            if meta["empty_response"]:
                reason_counts["empty_response_count"] += len(targets)
            elif meta["parse_error"]:
                reason_counts["parse_error_count"] += len(targets)
            elif meta["schema_mismatch"]:
                reason_counts["schema_mismatch_count"] += len(targets)
            else:
                matched = set(meta.get("matched_dates", set()))
                reason_counts["date_match_failure_count"] += sum(1 for row in targets if row["date"] not in matched)
        except Exception:
            parsed = [neutral_label(row["date"]) for row in targets]
            reason_counts["parse_error_count"] += len(targets)
        for item in parsed:
            results[item.date] = item
            normalized_sentiment_counts[item.sentiment_label] += 1
            normalized_flag_counts["fatigue"] += int(item.fatigue_flag)
            normalized_flag_counts["stress"] += int(item.stress_flag)
            normalized_flag_counts["social_load"] += int(item.social_load_flag)
            normalized_flag_counts["achievement"] += int(item.achievement_flag)
            normalized_flag_counts["self_care"] += int(item.self_care_flag)
            normalized_flag_counts["sleep_issue"] += int(item.sleep_issue_flag)
    logging.info("notes batch label count=%s", len(rows))
    logging.info("notes batch api calls=%s", api_calls)
    if audit is not None:
        audit.update(
            {
                "api_calls": api_calls,
                "fallback_reason_counts": dict(reason_counts),
                "raw_response_paths": raw_response_paths,
                "raw_sentiment_counts": dict(raw_sentiment_counts),
                "raw_flag_counts": dict(raw_flag_counts),
                "normalized_sentiment_counts": dict(normalized_sentiment_counts),
                "normalized_flag_counts": dict(normalized_flag_counts),
            }
        )
    return {row["date"]: results.get(row["date"], neutral_label(row["date"])) for row in rows}
