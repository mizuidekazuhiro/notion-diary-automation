"""Advice input types and history calculations; no HTTP, AI, or persistence.

Acquisition remains in mood_advice_generator's compatibility facade. The
calculation helpers consume already loaded DailyLogSummary values only.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence, TypedDict

from publish.read_daily_log import DailyLogSummary
from scripts.sleep_utils import resolve_sleep_duration_minutes, resolve_sleep_for_target_date
from scripts.generation_state import GenerationDetails, GenerationStatus


class AdviceAnalysis(TypedDict, total=False):
    generation: GenerationDetails
    final_status: GenerationStatus
    fallback_used: bool
    assessment_status: str
    target_counts: dict[str, int]
    risk_level: str
    evidence_used: list[str]
    stage_b_error: str | None


class AdviceJudgment(TypedDict, total=False):
    analysis_json: AdviceAnalysis
    analysis_audit: dict[str, Any]
    matched_pattern_count: int
    matched_patterns_count: int
    evidence_used: list[str]
    meal_signal: str
    notes_pattern_signal: str
    location_pattern_signal: str
    regression_summary: dict[str, Any]
    lightgbm_summary: dict[str, Any]


class TodayState(TypedDict):
    today_is_morning_incomplete: bool
    today_sleep: dict[str, Any]
    historical_behavior_patterns: dict[str, Any]
    historical_recording_patterns: dict[str, Any]
    historical_context: dict[str, Any]


class HistoricalComparison(TypedDict):
    counts: dict[str, int]
    comparisons: dict[str, Any]
    last_30_days_summary: dict[str, Any]
    top_good_days: list[dict[str, Any]]
    top_bad_days: list[dict[str, Any]]
    high_mood_sample_count: int
    low_mood_sample_count: int


class HistoryDiagnostics(TypedDict):
    history_requested_days: int
    history_loaded_count: int
    history_failed_count: int
    history_missing_count: int
    history_failed_dates: list[str]
    history_missing_dates: list[str]
    history_partial: bool
    history_incomplete: bool
    include_next_day: bool


class TodayAdviceContext(TypedDict):
    history: list[DailyLogSummary]
    historical_summaries: list[DailyLogSummary]
    today_summary: DailyLogSummary
    structured: HistoricalComparison
    today_state: TodayState
    notes_used: bool
    judgment_input: dict[str, Any]
    sleep_candidates: list[dict[str, Any]]
    selected_sleep_candidate: dict[str, Any] | None
    sleep_properties_source: str
    today_sleep_context: dict[str, Any]
    history_debug: HistoryDiagnostics
    reflection_date: str
    advice_target_date: str


LOOKBACK_DAYS = 30



RECENT_WINDOW_DAYS = 14



SHORT_WINDOW_DAYS = 7



SAMPLE_DAYS_PER_BUCKET = 5



MEAL_NUMERIC_FIELDS = ("kcal", "protein", "fat", "carb")



NOTES_SIGNAL_PATTERNS = {
    "fatigue": ["疲れ", "だる", "しんど", "倦怠", "疲労"],
    "sleep_issue": ["寝不足", "眠い", "眠気", "寝なかった", "寝れてない", "睡眠不足", "夜更かし"],
    "overeating": ["食べすぎ", "食べ過ぎ", "夜食", "食べすぎた", "食欲暴走", "食べ過ぎた"],
    "stress": ["喧嘩", "後悔", "ストレス", "イライラ", "不安", "焦り"],
    "focus": ["集中できた", "集中", "はかど", "捗", "進んだ", "没頭"],
    "exercise": ["ジム", "運動", "ランニング", "筋トレ", "散歩", "ストレッチ"],
    "recovery": ["体調が良い", "回復", "調子が良い", "元気", "持ち直", "楽になった"],
}



LOCATION_PATTERN_KEYS = ("home_heavy_day", "office_heavy_day", "outing_heavy_day", "late_outing_day", "multi_stop_day")



def _safe_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None



def _safe_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None



def _safe_int(value: object) -> Optional[int]:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return int(round(numeric))



def normalize_mood_to_score(raw_mood: object) -> Optional[int]:
    if raw_mood is None:
        return None
    if isinstance(raw_mood, (int, float)) and not isinstance(raw_mood, bool):
        score = int(round(float(raw_mood)))
        return score if 1 <= score <= 5 else None
    text = _safe_text(raw_mood)
    if not text:
        return None

    normalized = text.replace("☆", "★").replace("⭐", "★").replace("🌟", "★").replace("✩", "★")
    star_count = normalized.count("★") + normalized.count("⭐")
    if 1 <= star_count <= 5:
        return star_count

    digit_match = re.search(r"([1-5])", normalized)
    if digit_match:
        return int(digit_match.group(1))
    return None



def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)



def _delta(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base is None:
        return None
    return round(current - base, 2)



def _recording_rate(items: Sequence[DailyLogSummary], extractor: Any) -> Optional[float]:
    if not items:
        return None
    count = 0
    for item in items:
        value = extractor(item)
        if isinstance(value, str):
            if value.strip():
                count += 1
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            count += 1
        elif isinstance(value, list) and value:
            count += 1
    return round(count / len(items), 2)



def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "未記録"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")



def _resolve_today_sleep_candidates(*, target_date: str, today_summary: DailyLogSummary, history: Sequence[DailyLogSummary]) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]], str]:
    return resolve_sleep_for_target_date(
        target_date=target_date,
        today_summary=today_summary,
        history_summaries=history,
    )



def _build_metric_snapshot(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    return {
        "sleep_duration_min_avg": _mean([item.sleep_duration_min for item in items]),
        "sleep_score_avg": _mean([item.sleep_score for item in items]),
        "done_count_avg": _mean([_safe_float(item.done_count) for item in items]),
        "drop_count_avg": _mean([_safe_float(item.drop_count) for item in items]),
        "expenses_total_avg": _mean([item.expenses_total for item in items]),
    }



def _compare_metric_windows(current: Optional[float], base: Optional[float]) -> Optional[str]:
    if current is None or base is None:
        return None
    if current > base:
        return "up"
    if current < base:
        return "down"
    return "flat"



def _trend_direction(values: Sequence[Optional[float]]) -> Optional[str]:
    nums = [float(v) for v in values if v is not None]
    if len(nums) < 3:
        return None
    if nums[0] < nums[1] < nums[2]:
        return "up"
    if nums[0] > nums[1] > nums[2]:
        return "down"
    return None



def _meal_metric_avg(items: Sequence[DailyLogSummary], field_name: str) -> Optional[float]:
    return _mean([_safe_float(getattr(item, field_name, None)) for item in items])



def _extract_notes_signals(note: Optional[str]) -> dict[str, bool]:
    text = _safe_text(note)
    if not text:
        return {key: False for key in NOTES_SIGNAL_PATTERNS}
    lowered = text.lower()
    return {key: any(keyword in text or keyword in lowered for keyword in keywords) for key, keywords in NOTES_SIGNAL_PATTERNS.items()}



def _notes_signal_rates(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    if not items:
        return {f"{key}_rate": None for key in NOTES_SIGNAL_PATTERNS}
    counts = {key: 0 for key in NOTES_SIGNAL_PATTERNS}
    for item in items:
        signals = _extract_notes_signals(item.notes)
        for key, matched in signals.items():
            if matched:
                counts[key] += 1
    return {f"{key}_rate": round(counts[key] / len(items), 2) for key in NOTES_SIGNAL_PATTERNS}



def _extract_location_patterns(summary: Optional[str]) -> dict[str, bool]:
    from scripts.location_features import extract_location_flags
    return {key.replace("_flag", "_day"): value for key, value in extract_location_flags(summary or "").items()}



def _location_pattern_rates(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    observations = [_extract_location_patterns(item.location_summary) for item in items]
    return {f"{key}_rate": (round(sum(row[key] is True for row in observations) / sum(row[key] is not None for row in observations), 2)
            if any(row[key] is not None for row in observations) else None) for key in LOCATION_PATTERN_KEYS}



def _workout_snapshot(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    if not items:
        return {
            "workout_day_rate": None,
            "workout_sessions_avg": None,
            "workout_duration_min_avg": None,
            "workout_sets_avg": None,
            "workout_volume_kg_avg": None,
            "workout_calories_avg": None,
        }
    return {
        "workout_day_rate": round(sum(1 for item in items if item.workout_done) / len(items), 2),
        "workout_sessions_avg": _mean([_safe_float(item.workout_sessions) for item in items]),
        "workout_duration_min_avg": _mean([_safe_float(item.workout_duration_min) for item in items]),
        "workout_sets_avg": _mean([_safe_float(item.workout_sets) for item in items]),
        "workout_volume_kg_avg": _mean([_safe_float(item.workout_volume_kg) for item in items]),
        "workout_calories_avg": _mean([_safe_float(item.workout_calories) for item in items]),
    }



def _build_behavior_snapshot(items: Sequence[DailyLogSummary]) -> dict[str, Any]:
    snapshot = {
        **_build_metric_snapshot(items),
        "notes_recording_rate": _recording_rate(items, lambda item: item.notes),
        "meal_logged_rate": _recording_rate(items, lambda item: [item.meal_summary] if _safe_text(item.meal_summary) else item.meal_photos),
        "workout": _workout_snapshot(items),
    }
    for field in MEAL_NUMERIC_FIELDS:
        snapshot[f"{field}_avg"] = _meal_metric_avg(items, field)
    snapshot["notes_signals"] = _notes_signal_rates(items)
    snapshot["location_patterns"] = _location_pattern_rates(items)
    return snapshot



def _delta_map(current: Mapping[str, Optional[float]], base: Mapping[str, Optional[float]]) -> dict[str, Optional[float]]:
    keys = set(current.keys()) | set(base.keys())
    return {key: _delta(current.get(key), base.get(key)) for key in sorted(keys)}



def _build_today_state(today_summary: DailyLogSummary, recent_summaries: Sequence[DailyLogSummary]) -> TodayState:
    yesterday = recent_summaries[0] if recent_summaries else None
    recent_7 = list(recent_summaries[:SHORT_WINDOW_DAYS])
    recent_14 = list(recent_summaries[:RECENT_WINDOW_DAYS])
    recent_30 = list(recent_summaries[:LOOKBACK_DAYS])
    recent_3_chronological = list(reversed(recent_summaries[:3]))

    recent_7_metrics = _build_metric_snapshot(recent_7)
    recent_14_metrics = _build_metric_snapshot(recent_14)
    recent_30_metrics = _build_metric_snapshot(recent_30)

    today_sleep_duration = resolve_sleep_duration_minutes(
        today_summary.sleep_start,
        today_summary.sleep_end,
        today_summary.sleep_duration_min,
    ).resolved_sleep_duration_min
    today_sleep_score = _safe_float(today_summary.sleep_score)

    return {
        "today_is_morning_incomplete": True,
        "today_sleep": {
            "sleep_analysis_jp": today_summary.sleep_analysis_jp,
            "today_condition_forecast_jp": today_summary.today_condition_forecast_jp,
            "sleep_start": today_summary.sleep_start or "未記録",
            "sleep_end": today_summary.sleep_end or "未記録",
            "sleep_duration_min": today_summary.sleep_duration_min,
            "resolved_sleep_duration_min": today_sleep_duration,
            "sleep_score": today_summary.sleep_score,
            "sleep_heart_rate": today_summary.sleep_heart_rate,
            "deep_duration_min": today_summary.deep_duration_min,
            "rem_duration_min": today_summary.rem_duration_min,
            "readiness_stars": today_summary.readiness_stars,
            "readiness_hrv": today_summary.readiness_hrv,
            "readiness_bpm": today_summary.readiness_bpm,
            "baseline_hrv": today_summary.baseline_hrv,
            "baseline_waking_bpm": today_summary.baseline_waking_bpm,
            "comparisons": {
                "vs_yesterday": {
                    "sleep_duration_min_delta": _delta(today_sleep_duration, _safe_float(yesterday.sleep_duration_min) if yesterday else None),
                    "sleep_score_delta": _delta(today_sleep_score, _safe_float(yesterday.sleep_score) if yesterday else None),
                },
                "vs_recent_7d_avg": {
                    "sleep_duration_min_delta": _delta(today_sleep_duration, recent_7_metrics["sleep_duration_min_avg"]),
                    "sleep_score_delta": _delta(today_sleep_score, recent_7_metrics["sleep_score_avg"]),
                },
                "recent_7d_avg": {
                    "sleep_duration_min_avg": recent_7_metrics["sleep_duration_min_avg"],
                    "sleep_score_avg": recent_7_metrics["sleep_score_avg"],
                },
                "recent_14d_avg": {
                    "sleep_duration_min_avg": recent_14_metrics["sleep_duration_min_avg"],
                    "sleep_score_avg": recent_14_metrics["sleep_score_avg"],
                },
            },
            "recent_3day_trend": {
                "sleep_duration_min": _trend_direction([item.sleep_duration_min for item in recent_3_chronological]),
                "sleep_score": _trend_direction([item.sleep_score for item in recent_3_chronological]),
            },
        },
        "historical_behavior_patterns": {
            "recent_7d_avg": recent_7_metrics,
            "recent_14d_avg": recent_14_metrics,
            "recent_30d_avg": recent_30_metrics,
            "recent_7d_vs_30d": {
                "done_count": _compare_metric_windows(recent_7_metrics["done_count_avg"], recent_30_metrics["done_count_avg"]),
                "drop_count": _compare_metric_windows(recent_7_metrics["drop_count_avg"], recent_30_metrics["drop_count_avg"]),
                "spend_total": _compare_metric_windows(recent_7_metrics["expenses_total_avg"], recent_30_metrics["expenses_total_avg"]),
            },
            "recent_14d_trend": {
                "done_count": _trend_direction([_safe_float(item.done_count) for item in reversed(recent_summaries[:14])]),
                "drop_count": _trend_direction([_safe_float(item.drop_count) for item in reversed(recent_summaries[:14])]),
                "spend_total": _trend_direction([_safe_float(item.expenses_total) for item in reversed(recent_summaries[:14])]),
            },
            "workout_pattern": {
                "recent_7d": _workout_snapshot(recent_7),
                "recent_14d": _workout_snapshot(recent_14),
                "recent_30d": _workout_snapshot(recent_30),
                "recent_14d_duration_trend": _trend_direction([_safe_float(item.workout_duration_min) for item in reversed(recent_summaries[:14])]),
                "recent_14d_volume_trend": _trend_direction([_safe_float(item.workout_volume_kg) for item in reversed(recent_summaries[:14])]),
            },
            "meal_mood_comparison": {
                "recent_7d": {f"{field}_avg": _meal_metric_avg(recent_7, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_14d": {f"{field}_avg": _meal_metric_avg(recent_14, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_30d": {f"{field}_avg": _meal_metric_avg(recent_30, field) for field in MEAL_NUMERIC_FIELDS},
            },
        },
        "historical_recording_patterns": {
            "notes_recording_rate_7d": _recording_rate(recent_7, lambda item: item.notes),
            "notes_recording_rate_14d": _recording_rate(recent_14, lambda item: item.notes),
            "meal_logged_rate_7d": _recording_rate(recent_7, lambda item: [item.meal_summary] if _safe_text(item.meal_summary) else item.meal_photos),
            "meal_logged_rate_14d": _recording_rate(recent_14, lambda item: [item.meal_summary] if _safe_text(item.meal_summary) else item.meal_photos),
            "location_recording_rate_7d": _recording_rate(recent_7, lambda item: item.location_summary),
            "location_recording_rate_14d": _recording_rate(recent_14, lambda item: item.location_summary),
        },
        "historical_context": {
            "recent_7d_location_samples": [item.location_summary for item in recent_7 if _safe_text(item.location_summary)],
            "recent_notes_samples": [item.notes for item in recent_14 if _safe_text(item.notes)][:5],
            "historical_daily_score_avg": _mean([normalize_mood_to_score(item.mood) for item in recent_30]),
            "notes_signal_comparison": {
                "recent_7d": _notes_signal_rates(recent_7),
            },
            "location_pattern_comparison": {
                "recent_7d": _location_pattern_rates(recent_7),
            },
            "workout_pattern": {
                "recent_7d": _workout_snapshot(recent_7),
                "recent_14d": _workout_snapshot(recent_14),
                "recent_30d": _workout_snapshot(recent_30),
            },
        },
    }



def _build_day_record(summary: DailyLogSummary) -> dict[str, Any]:
    return {
        "date": summary.target_date,
        "sleep_start": summary.sleep_start,
        "sleep_end": summary.sleep_end,
        "sleep_duration_min": summary.sleep_duration_min,
        "sleep_score": summary.sleep_score,
        "location_summary": summary.location_summary,
        "meal_summary": summary.meal_summary,
        "meal_logged": bool(_safe_text(summary.meal_summary) or summary.meal_photos),
        "done_count": summary.done_count,
        "drop_count": summary.drop_count,
        "spend_total": summary.expenses_total,
        "notes": summary.notes,
        "kcal": summary.kcal,
        "protein": summary.protein,
        "fat": summary.fat,
        "carb": summary.carb,
        "workout_done": summary.workout_done,
        "workout_sessions": summary.workout_sessions,
        "workout_gym": summary.workout_gym,
        "workout_duration_min": summary.workout_duration_min,
        "workout_sets": summary.workout_sets,
        "workout_volume_kg": summary.workout_volume_kg,
        "workout_calories": summary.workout_calories,
        "workout_exercises": summary.workout_exercises,
        "workout_summary": summary.workout_summary,
        "daily_score": normalize_mood_to_score(summary.mood),
    }



def _select_top_days(items: Sequence[DailyLogSummary], *, descending: bool, limit: int = SAMPLE_DAYS_PER_BUCKET) -> list[DailyLogSummary]:
    scored_items = []
    for item in items:
        score = normalize_mood_to_score(item.mood)
        if score is None:
            continue
        scored_items.append((item, score, _safe_float(item.sleep_score) or -1.0, _safe_float(item.done_count) or -1.0, item.target_date))
    if descending:
        filtered = [entry for entry in scored_items if entry[1] >= 4]
        filtered.sort(key=lambda entry: (-entry[1], -(entry[2]), -(entry[3]), entry[4]))
    else:
        filtered = [entry for entry in scored_items if entry[1] <= 2]
        filtered.sort(key=lambda entry: (entry[1], -(entry[2]), -(entry[3]), entry[4]))
    if len(filtered) <= limit:
        return [item for item, *_ in filtered]
    step = max(1, len(filtered) // limit)
    selected: list[DailyLogSummary] = []
    for index in range(0, len(filtered), step):
        selected.append(filtered[index][0])
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for item, *_ in filtered:
            if item not in selected:
                selected.append(item)
            if len(selected) >= limit:
                break
    return selected[:limit]



def _build_structured_comparison(history: Sequence[DailyLogSummary]) -> HistoricalComparison:
    scored = [(item, normalize_mood_to_score(item.mood)) for item in history]
    high = [item for item, mood in scored if mood in {4, 5}]
    low = [item for item, mood in scored if mood in {1, 2}]
    middle = [item for item, mood in scored if mood == 3]
    recent_7 = list(history[:SHORT_WINDOW_DAYS])
    recent_14 = list(history[:RECENT_WINDOW_DAYS])
    top_good_days = _select_top_days(history, descending=True)
    top_bad_days = _select_top_days(history, descending=False)
    last_30_days = [_build_day_record(item) for item in history[:LOOKBACK_DAYS]]

    def compare(items: Sequence[DailyLogSummary]) -> dict[str, Any]:
        return {
            "count": len(items),
            **_build_behavior_snapshot(items),
        }

    notes_used_count = sum(1 for item in history if _safe_text(item.notes))
    return {
        "counts": {
            "history_days": len(history),
            "recent_7d_days": len(recent_7),
            "recent_14d_days": len(recent_14),
            "high_mood_days": len(high),
            "low_mood_days": len(low),
            "middle_mood_days": len(middle),
            "mood_recorded_days": sum(1 for _, mood in scored if mood is not None),
            "last_30_days_count": len(last_30_days),
            "top_good_days_count": len(top_good_days),
            "top_bad_days_count": len(top_bad_days),
            "notes_used_count": notes_used_count,
            "diary_used": False,
        },
        "comparisons": {
            "recent_7d": compare(recent_7),
            "recent_14d": compare(recent_14),
            "recent_30d": compare(history[:LOOKBACK_DAYS]),
            "high_mood": compare(high),
            "low_mood": compare(low),
            "middle_mood": compare(middle),
            "meal_mood_comparison": {
                "high_mood": {f"{field}_avg": _meal_metric_avg(high, field) for field in MEAL_NUMERIC_FIELDS},
                "low_mood": {f"{field}_avg": _meal_metric_avg(low, field) for field in MEAL_NUMERIC_FIELDS},
                "middle_mood": {f"{field}_avg": _meal_metric_avg(middle, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_7d": {f"{field}_avg": _meal_metric_avg(recent_7, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_14d": {f"{field}_avg": _meal_metric_avg(recent_14, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_30d": {f"{field}_avg": _meal_metric_avg(history[:LOOKBACK_DAYS], field) for field in MEAL_NUMERIC_FIELDS},
                "good_vs_bad_delta": {
                    "kcal": _delta(_meal_metric_avg(top_good_days, "kcal"), _meal_metric_avg(top_bad_days, "kcal")),
                    "protein": _delta(_meal_metric_avg(top_good_days, "protein"), _meal_metric_avg(top_bad_days, "protein")),
                    "fat": _delta(_meal_metric_avg(top_good_days, "fat"), _meal_metric_avg(top_bad_days, "fat")),
                    "carb": _delta(_meal_metric_avg(top_good_days, "carb"), _meal_metric_avg(top_bad_days, "carb")),
                },
            },
            "notes_signal_comparison": {
                "high_mood": _notes_signal_rates(high),
                "low_mood": _notes_signal_rates(low),
                "recent_7d": _notes_signal_rates(recent_7),
                "good_vs_bad_delta": _delta_map(_notes_signal_rates(top_good_days), _notes_signal_rates(top_bad_days)),
            },
            "location_pattern_comparison": {
                "high_mood": _location_pattern_rates(high),
                "low_mood": _location_pattern_rates(low),
                "recent_7d": _location_pattern_rates(recent_7),
                "good_vs_bad_delta": _delta_map(_location_pattern_rates(top_good_days), _location_pattern_rates(top_bad_days)),
            },
            "workout_comparison": {
                "high_mood": _workout_snapshot(high),
                "low_mood": _workout_snapshot(low),
                "recent_7d": _workout_snapshot(recent_7),
                "recent_14d": _workout_snapshot(recent_14),
                "recent_30d": _workout_snapshot(history[:LOOKBACK_DAYS]),
                "good_days": _workout_snapshot(top_good_days),
                "bad_days": _workout_snapshot(top_bad_days),
            },
            "good_vs_bad_delta": {
                "sleep_duration_min": _delta(_build_metric_snapshot(top_good_days)["sleep_duration_min_avg"], _build_metric_snapshot(top_bad_days)["sleep_duration_min_avg"]),
                "sleep_score": _delta(_build_metric_snapshot(top_good_days)["sleep_score_avg"], _build_metric_snapshot(top_bad_days)["sleep_score_avg"]),
                "done_count": _delta(_build_metric_snapshot(top_good_days)["done_count_avg"], _build_metric_snapshot(top_bad_days)["done_count_avg"]),
                "drop_count": _delta(_build_metric_snapshot(top_good_days)["drop_count_avg"], _build_metric_snapshot(top_bad_days)["drop_count_avg"]),
                "spend_total": _delta(_build_metric_snapshot(top_good_days)["expenses_total_avg"], _build_metric_snapshot(top_bad_days)["expenses_total_avg"]),
                "kcal": _delta(_meal_metric_avg(top_good_days, "kcal"), _meal_metric_avg(top_bad_days, "kcal")),
                "protein": _delta(_meal_metric_avg(top_good_days, "protein"), _meal_metric_avg(top_bad_days, "protein")),
                "fat": _delta(_meal_metric_avg(top_good_days, "fat"), _meal_metric_avg(top_bad_days, "fat")),
                "carb": _delta(_meal_metric_avg(top_good_days, "carb"), _meal_metric_avg(top_bad_days, "carb")),
            },
        },
        "last_30_days_summary": {
            "daily_records": last_30_days,
            "aggregates": {
                "all_days": compare(history),
                "recent_7d": compare(recent_7),
                "recent_14d": compare(recent_14),
                "top_good_days": compare(top_good_days),
                "top_bad_days": compare(top_bad_days),
            },
        },
        "top_good_days": [_build_day_record(item) for item in top_good_days],
        "top_bad_days": [_build_day_record(item) for item in top_bad_days],
        "high_mood_sample_count": len(top_good_days),
        "low_mood_sample_count": len(top_bad_days),
    }
