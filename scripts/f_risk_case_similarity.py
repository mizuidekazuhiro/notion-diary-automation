from __future__ import annotations

from typing import Any

from scripts.f_risk_case_patterns import FRiskEventCase

FEATURE_KEYS = [
    "sleep_hours", "sleep_short_streak", "bedtime_min", "sleep_score", "sleep_hours_lag_1", "sleep_hours_rolling_mean_3d",
    "notes_stress_flag", "notes_fatigue_flag", "notes_social_load_flag", "notes_sleep_issue_flag", "notes_has_drinking",
    "notes_has_late_work", "notes_has_social", "notes_has_regret", "notes_has_conflict", "late_outing_flag", "multi_stop_flag",
    "outing_heavy_flag", "home_heavy_flag", "movement_intensity_like_flag", "social_event_like_flag", "spending_total",
    "spending_vs_7d_delta", "spending_total_rolling_mean_7d", "social_spend_like_flag", "convenience_store_like_flag",
    "kcal_vs_7d_delta", "fat_vs_7d_delta", "carb_vs_7d_delta", "high_fat_flag", "high_carb_flag", "task_completion_ratio",
    "done_vs_7d_delta", "drop_vs_7d_delta", "schedule_same_day_event_count", "late_event_like_flag", "weather_bad_flag",
    "weather_precip_probability_max", "weather_temp_range_c",
]


def compute_case_similarity(*, recent_case: dict[str, Any], event_cases: list[FRiskEventCase], top_n: int) -> dict[str, Any]:
    recent_rows = list(recent_case.get("recent_rows") or [])
    if not event_cases or not recent_rows:
        return _empty_similarity("過去Fケースとの比較対象が不足")

    scored: list[dict[str, Any]] = []
    for case in event_cases:
        if not case.pre_rows:
            continue
        overlap = feature_overlap_score(recent_rows=recent_rows, pre_rows=case.pre_rows)
        sequence = sequence_score(recent_signature=recent_case.get("pre_signature") or {}, case_signature=case.pre_signature)
        type_score = event_type_score(recent_rows=recent_rows, event_type=case.event_type)
        total = round((0.5 * overlap) + (0.35 * sequence) + (0.15 * type_score), 3)
        scored.append({
            "event_date": case.event_date,
            "event_type": case.event_type,
            "score_total": total,
            "score_overlap": overlap,
            "score_sequence": sequence,
            "score_type": type_score,
            "matched_features": matched_feature_names(recent_rows=recent_rows, pre_rows=case.pre_rows),
        })

    if not scored:
        return _empty_similarity("比較可能な過去F pre-windowが不足")

    scored.sort(key=lambda x: x["score_total"], reverse=True)
    top = scored[: max(1, top_n)]
    best = top[0]
    strength = "strong" if best["score_total"] >= 0.72 else "medium" if best["score_total"] >= 0.55 else "weak"
    return {
        "strength": strength,
        "summary": f"直近{len(recent_rows)}日の流れは過去F前兆と{strength}一致（最良一致 {best['event_date']}）",
        "top_case_matches": top,
        "top_case_match_scores": [m["score_total"] for m in top],
        "matched_case_dates": [str(m["event_date"]) for m in top],
        "matched_case_types": [str(m["event_type"]) for m in top],
        "matched_pre_patterns": top[0].get("matched_features", [])[:6],
        "score_total": best["score_total"],
        "score_overlap": best["score_overlap"],
        "score_sequence": best["score_sequence"],
        "score_type": best["score_type"],
        "usable_f_event_count": len(scored),
    }


def _empty_similarity(summary: str) -> dict[str, Any]:
    return {
        "strength": "weak",
        "summary": summary,
        "top_case_matches": [],
        "top_case_match_scores": [],
        "matched_case_dates": [],
        "matched_case_types": [],
        "matched_pre_patterns": [],
        "score_total": 0.0,
        "score_overlap": 0.0,
        "score_sequence": 0.0,
        "score_type": 0.0,
        "usable_f_event_count": 0,
    }


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def feature_overlap_score(*, recent_rows: list[dict[str, Any]], pre_rows: list[dict[str, Any]]) -> float:
    recent = recent_rows[-1]
    base = pre_rows[-1]
    compared = 0
    matched = 0
    for key in FEATURE_KEYS:
        r = _num(recent.get(key))
        b = _num(base.get(key))
        if r is None and b is None:
            continue
        compared += 1
        if r is None or b is None:
            continue
        tolerance = max(0.2, abs(b) * 0.35)
        if abs(r - b) <= tolerance:
            matched += 1
    return round((matched / compared), 3) if compared else 0.0


def sequence_score(*, recent_signature: dict[str, Any], case_signature: dict[str, Any]) -> float:
    recent_seq = list(recent_signature.get("sequence") or [])
    case_seq = list(case_signature.get("sequence") or [])
    if not recent_seq or not case_seq:
        return 0.0
    size = min(len(recent_seq), len(case_seq))
    total = 0.0
    denom = 0.0
    for i in range(1, size + 1):
        rs = set(recent_seq[-i])
        cs = set(case_seq[-i])
        if not rs and not cs:
            continue
        weight = 1.25 if i == 1 else 1.0
        denom += weight
        total += weight * (len(rs & cs) / max(1, len(rs | cs)))
    return round(total / denom, 3) if denom else 0.0


def event_type_score(*, recent_rows: list[dict[str, Any]], event_type: str) -> float:
    latest = recent_rows[-1] if recent_rows else {}
    if event_type == "night_outing":
        return 1.0 if bool(latest.get("late_outing_flag")) else 0.3
    if event_type == "drinking_social":
        return 1.0 if bool(latest.get("notes_has_drinking")) and bool(latest.get("notes_social_load_flag")) else 0.25
    if event_type == "impulse_spend":
        return 1.0 if (_num(latest.get("spending_vs_7d_delta")) or 0) >= 2500 else 0.3
    if event_type == "stress_release":
        return 1.0 if bool(latest.get("notes_stress_flag")) else 0.3
    if event_type == "commute_detour":
        return 1.0 if bool(latest.get("multi_stop_flag")) else 0.3
    return 0.2


def matched_feature_names(*, recent_rows: list[dict[str, Any]], pre_rows: list[dict[str, Any]]) -> list[str]:
    recent = recent_rows[-1]
    base = pre_rows[-1]
    names: list[str] = []
    labels = {
        "sleep_short_streak": "短睡眠連続",
        "notes_social_load_flag": "social load",
        "notes_stress_flag": "stress",
        "late_outing_flag": "夜外出傾向",
        "notes_has_drinking": "drinking",
        "spending_vs_7d_delta": "支出増加",
    }
    for key, label in labels.items():
        r = _num(recent.get(key))
        b = _num(base.get(key))
        if r is None or b is None:
            continue
        if abs(r - b) <= max(0.2, abs(b) * 0.35):
            names.append(label)
    return names
