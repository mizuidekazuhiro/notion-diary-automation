from __future__ import annotations

from typing import Any

ACTION_MAP = {
    "sleep": ["午前中の重い判断を絞る", "昼に短く休む", "夜の予定を増やしすぎない"],
    "stress": ["必須タスクを3件以内に絞る", "先延ばししやすい作業を朝一で片付ける", "回復優先にする"],
    "social": ["会食や長時間の対人予定を増やしすぎない", "一人で整える時間を確保する"],
    "positive": ["良い流れを維持する", "朝の集中作業を先に置く"],
}


def _confidence(sample_size: int, delta: float) -> str:
    if sample_size >= 5 and delta >= 0.30:
        return "high"
    if sample_size >= 3 and delta >= 0.20:
        return "medium"
    return "low"


def analyze_lag_patterns(df: Any) -> dict[str, Any]:
    work = df.copy().sort_values("date").reset_index(drop=True)
    work["next_day_low_mood_flag"] = work["mood"].shift(-1).fillna(5) <= 2
    work["next_day_fatigue_flag"] = work["notes_fatigue_flag"].shift(-1).fillna(False)
    work["next_day_low_productivity_flag"] = (work["task_drop_count"].shift(-1).fillna(0) >= 2) | (work["task_done_count"].shift(-1).fillna(0) <= 1)
    conditions = {
        "prev_sleep_lt_6h": work["sleep_lt_6h_flag"],
        "prev_bedtime_after_0100": work["bedtime_after_0100_flag"],
        "prev_sleep_score_low": work["sleep_score"].fillna(100) < 70,
        "prev_notes_negative": work["notes_sentiment_label"] == "negative",
        "prev_notes_fatigue": work["notes_fatigue_flag"],
        "prev_notes_stress": work["notes_stress_flag"],
        "prev_notes_social_load": work["notes_social_load_flag"],
        "prev_notes_sleep_issue": work["notes_sleep_issue_flag"],
    }
    baseline = float(work["next_day_low_mood_flag"].mean()) if len(work) else 0.0
    all_patterns = []
    adopted = []
    for key, cond in conditions.items():
        subset = work[cond.fillna(False)]
        sample = int(len(subset))
        hit = float(subset["next_day_low_mood_flag"].mean()) if sample else 0.0
        delta = round(hit - baseline, 2)
        conf = _confidence(sample, delta)
        item = {
            "pattern_id": key,
            "target_outcome": "next_day_low_mood_flag",
            "sample_size": sample,
            "hit_rate": round(hit, 2),
            "baseline_rate": round(baseline, 2),
            "delta": delta,
            "confidence": conf,
        }
        all_patterns.append(item)
        if sample >= 3 and delta >= 0.20 and hit >= 0.50:
            item = dict(item)
            if "sleep" in key:
                item["recommended_actions"] = ACTION_MAP["sleep"][:2]
            elif "stress" in key or "fatigue" in key:
                item["recommended_actions"] = ACTION_MAP["stress"][:2]
            elif "social" in key:
                item["recommended_actions"] = ACTION_MAP["social"][:2]
            else:
                item["recommended_actions"] = ACTION_MAP["positive"][:2]
            adopted.append(item)
    return {"all_patterns": all_patterns, "adopted_patterns": adopted}
