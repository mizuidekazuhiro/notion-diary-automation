from __future__ import annotations

import json
import logging
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
    fallback = {str(row.get("date")): neutral_label(str(row.get("date"))) for row in input_rows}
    try:
        parsed = json.loads(raw_text)
    except Exception:
        return list(fallback.values())
    if not isinstance(parsed, list):
        return list(fallback.values())
    for row in parsed:
        if not isinstance(row, Mapping):
            continue
        date = str(row.get("date") or "").strip()
        if date not in fallback:
            continue
        fallback[date] = _normalize_item(row, date)
    return [fallback[str(row.get("date"))] for row in input_rows]


def label_notes_in_batches(
    *,
    summaries: Sequence[DailyLogSummary],
    chat_completion: Callable[..., str],
    model: str,
    batch_size: int = 15,
) -> dict[str, NoteLabel]:
    rows = [{"date": s.target_date, "notes": (s.notes or "").strip()} for s in summaries]
    if not rows:
        return {}
    api_calls = 0
    results: dict[str, NoteLabel] = {}
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
            parsed = parse_note_label_json(raw, targets)
        except Exception:
            parsed = [neutral_label(row["date"]) for row in targets]
        for item in parsed:
            results[item.date] = item
    logging.info("notes batch label count=%s", len(rows))
    logging.info("notes batch api calls=%s", api_calls)
    return {row["date"]: results.get(row["date"], neutral_label(row["date"])) for row in rows}
