from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from publish.read_daily_log import DailyLogSummary
from scripts.note_batch_labeler import NoteLabel
from scripts.sleep_utils import resolve_sleep_duration_minutes, resolve_sleep_target_date


def _normalize_mood_to_score(raw_mood: object) -> float | None:
    if raw_mood is None:
        return None
    text = str(raw_mood)
    stars = text.replace("☆", "★").replace("⭐", "★").count("★")
    if 1 <= stars <= 5:
        return float(stars)
    for c in text:
        if c in "12345":
            return float(int(c))
    return None


def _to_minutes(dt_text: str | None) -> float:
    if not dt_text or "T" not in dt_text:
        return float("nan")
    try:
        hhmm = dt_text.split("T", 1)[1][:5]
        hour, minute = hhmm.split(":")
        return float(int(hour) * 60 + int(minute))
    except Exception:
        return float("nan")


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sleep_validity(summary: DailyLogSummary) -> tuple[bool, str | None, float | None]:
    all_invalid = summary.sleep_duration_min is None and summary.sleep_start is None and summary.sleep_end is None
    if all_invalid:
        return False, "missing_all_sleep_fields", None
    resolved = resolve_sleep_duration_minutes(summary.sleep_start, summary.sleep_end, summary.sleep_duration_min)
    duration = resolved.resolved_sleep_duration_min
    if duration is None:
        reason = resolved.invalid_reason or "missing_duration"
        score = _safe_float(summary.sleep_score)
        if reason == "duration_non_positive" and score == 0:
            return False, "zero_duration_and_score_zero", None
        return False, reason, None
    score = _safe_float(summary.sleep_score)
    if duration <= 0:
        if duration == 0 and score == 0:
            return False, "zero_duration_and_score_zero", None
        return False, "duration_non_positive", None
    if duration < 30 and score == 0:
        return False, "stub_sleep_window", duration
    return True, None, duration


def _extract_location_flags(text: str) -> dict[str, bool]:
    normalized = text.lower()
    return {
        "late_outing_flag": ("late_outing_day" in normalized) or ("late outing" in normalized),
        "multi_stop_flag": ("multi_stop_day" in normalized) or ("multi stop" in normalized),
        "home_heavy_flag": ("home_heavy_day" in normalized) or ("home heavy" in normalized) or ("自宅中心" in text),
        "office_heavy_flag": ("office_heavy_day" in normalized) or ("office heavy" in normalized) or ("オフィス" in text),
        "outing_heavy_flag": ("outing_heavy_day" in normalized) or ("outing heavy" in normalized),
    }


def build_daily_feature_table(histories: Sequence[DailyLogSummary], note_labels: Mapping[str, NoteLabel]) -> Any:
    import importlib

    pd_spec = importlib.util.find_spec("pandas")
    if pd_spec is None:
        raise RuntimeError("pandas is required for today advice feature table")
    pd = importlib.import_module("pandas")
    np = importlib.import_module("numpy")

    rows = []
    for item in histories:
        label = note_labels.get(item.target_date) or NoteLabel(
            date=item.target_date,
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
        sleep_valid_flag, sleep_invalid_reason, sleep_duration = _sleep_validity(item)
        location_flags = _extract_location_flags(item.location_summary or "")
        meal_text = item.meal_summary or ""
        notes_text = item.notes or ""
        activity_text = item.activity_summary or ""
        done = int(item.done_count or 0)
        drop = int(item.drop_count or 0)
        denom = done + drop

        rows.append(
            {
                "date": item.target_date,
                "sleep_target_date": resolve_sleep_target_date(
                    sleep_start=item.sleep_start,
                    sleep_end=item.sleep_end,
                    fallback_date=item.target_date,
                ),
                "mood": _normalize_mood_to_score(item.mood),
                "sleep_valid_flag": sleep_valid_flag,
                "sleep_invalid_reason": sleep_invalid_reason,
                "sleep_hours": (sleep_duration / 60.0) if sleep_valid_flag and sleep_duration is not None else np.nan,
                "sleep_score": _safe_float(item.sleep_score) if sleep_valid_flag else np.nan,
                "bedtime_min": _to_minutes(item.sleep_start) if sleep_valid_flag else np.nan,
                "wake_time_min": _to_minutes(item.sleep_end) if sleep_valid_flag else np.nan,
                "deep_duration_min": _safe_float(item.deep_duration_min) if sleep_valid_flag else np.nan,
                "rem_duration_min": _safe_float(item.rem_duration_min) if sleep_valid_flag else np.nan,
                "readiness_bpm": _safe_float(item.readiness_bpm),
                "readiness_hrv": _safe_float(item.readiness_hrv),
                "baseline_hrv": _safe_float(item.baseline_hrv),
                "baseline_waking_bpm": _safe_float(item.baseline_waking_bpm),
                "notes_present_flag": bool(notes_text.strip()),
                "notes_sentiment_label": label.sentiment_label,
                "notes_sentiment_score": int(label.sentiment_score),
                "sentiment_unknown": label.sentiment_label == "unknown",
                "no_signal_note": bool(label.no_signal_note),
                "parse_low_confidence": bool(label.parse_low_confidence),
                "tag_extract_failed": bool(label.tag_extract_failed),
                "notes_fatigue_flag": bool(label.fatigue_flag),
                "notes_stress_flag": bool(label.stress_flag),
                "notes_social_load_flag": bool(label.social_load_flag),
                "notes_achievement_flag": bool(label.achievement_flag),
                "notes_self_care_flag": bool(label.self_care_flag),
                "notes_sleep_issue_flag": bool(label.sleep_issue_flag),
                "notes_has_exercise": bool(label.derived_flags.get("exercise") or any(s.get("tag") in {"exercise","gym"} for s in label.signals)),
                "notes_has_social": any(s.get("tag") == "social" for s in label.signals),
                "notes_has_drinking": any(s.get("tag") == "drinking" for s in label.signals),
                "notes_has_conflict": any(s.get("tag") == "conflict" for s in label.signals),
                "notes_has_regret": any(s.get("tag") == "regret" for s in label.signals),
                "notes_has_productive": any(s.get("tag") == "productive" for s in label.signals),
                "notes_has_moderate_productivity": any(s.get("tag") == "moderate_productivity" for s in label.signals),
                "notes_has_money_saved": any(s.get("tag") == "money_saved" for s in label.signals),
                "notes_has_diet_disruption": any(s.get("tag") == "meal_disruption" for s in label.signals),
                "notes_has_late_work": any(s.get("tag") == "late_work" for s in label.signals),
                "notes_has_early_home": any(s.get("tag") == "early_home" for s in label.signals),
                "notes_has_business_trip": any(s.get("tag") == "business_trip" for s in label.signals),
                "notes_has_dc_work": any(s.get("tag") == "dc_work" for s in label.signals),
                "notes_has_presentation_work": any(s.get("tag") == "presentation_work" for s in label.signals),
                "notes_signal_count": len(label.signals),
                "notes_positive_signal_count": sum(1 for s in label.signals if s.get("polarity") == "positive"),
                "notes_negative_signal_count": sum(1 for s in label.signals if s.get("polarity") == "negative"),
                "notes_behavior_signal_count": sum(1 for s in label.signals if s.get("category") == "behavior"),
                "notes_state_signal_count": sum(1 for s in label.signals if s.get("category") == "state"),
                "notes_avg_confidence": (sum(float(s.get("confidence") or 0.0) for s in label.signals) / len(label.signals)) if label.signals else 0.0,
                "notes_parse_quality_score": {"low": 0.2, "medium": 0.6, "high": 1.0}.get(label.parse_quality, 0.2),
                "notes_recovery_like_flag": bool(label.derived_flags.get("recovery_like_flag")),
                "notes_self_control_flag": bool(label.derived_flags.get("self_control_flag")),
                "notes_work_progress_flag": bool(label.derived_flags.get("work_progress_flag")),
                "notes_life_disruption_flag": bool(label.derived_flags.get("life_disruption_flag")),
                "meal_logged_flag": bool(meal_text.strip()) or any(v is not None for v in (item.kcal, item.protein, item.fat, item.carb)),
                "kcal": _safe_float(item.kcal),
                "protein": _safe_float(item.protein),
                "fat": _safe_float(item.fat),
                "carb": _safe_float(item.carb),
                "overeating_like_flag": bool(re.search(r"食べすぎ|食べ過ぎ|夜食|暴食", meal_text)),
                "eating_out_like_flag": bool(re.search(r"外食|レストラン|居酒屋|カフェ", meal_text)),
                "late_meal_like_flag": bool(re.search(r"夜食|深夜|遅い", meal_text)),
                "spending_total": _safe_float(item.expenses_total),
                "expense_f_count": _safe_float(item.expense_f_count),
                "expense_f_total": _safe_float(item.expense_f_total),
                "transport_spend_like_flag": bool(re.search(r"交通|電車|タクシー|バス", notes_text + activity_text)),
                "social_spend_like_flag": bool(re.search(r"会食|飲み会|友人|同僚", notes_text + activity_text)),
                "task_done_count": done,
                "task_drop_count": drop,
                "task_completion_ratio": (done / denom) if denom > 0 else np.nan,
                "done_low_flag": done <= 1,
                "drop_high_flag": drop >= 2,
                "task_balance_bad_flag": bool(denom >= 2 and (drop / denom) >= 0.6),
                "location_present_flag": bool((item.location_summary or "").strip()),
                "social_event_like_flag": bool(label.social_load_flag or re.search(r"会食|飲み会|打ち合わせ|会議", notes_text + activity_text)),
                "movement_intensity_like_flag": bool(re.search(r"歩いた|移動|外出|ランニング|運動", notes_text + activity_text)),
                "weather_location": item.weather_location,
                "weather_summary": item.weather_summary,
                "weather_temp_max_c": _safe_float(item.weather_temp_max_c),
                "weather_temp_min_c": _safe_float(item.weather_temp_min_c),
                "weather_precip_probability_max": _safe_float(item.weather_precip_probability_max),
                "weather_code": _safe_float(item.weather_code),
                "is_weekend": _is_weekend(item.target_date),
                **location_flags,
            }
        )

    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    numeric_targets = [
        "sleep_hours", "sleep_score", "kcal", "protein", "fat", "carb", "spending_total", "task_done_count", "task_drop_count"
    ]
    for col in numeric_targets:
        if col not in df:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        if col.startswith("sleep"):
            series = series.where(df["sleep_valid_flag"].fillna(False))
        baseline = series.rolling(7, min_periods=2).mean().shift(1)
        df[col] = series
        df[f"{col}_vs_7d_delta"] = series - baseline

    df["done_vs_7d_delta"] = df["task_done_count"] - df["task_done_count"].rolling(7, min_periods=2).mean().shift(1)
    df["drop_vs_7d_delta"] = df["task_drop_count"] - df["task_drop_count"].rolling(7, min_periods=2).mean().shift(1)
    df["spending_vs_7d_delta"] = df["spending_total"] - df["spending_total"].rolling(7, min_periods=2).mean().shift(1)
    df["sleep_vs_7d_delta"] = df.get("sleep_hours_vs_7d_delta", np.nan)
    df["sleep_score_vs_7d_delta"] = df.get("sleep_score_vs_7d_delta", np.nan)

    q = df["spending_total"].dropna()
    threshold = float(q.quantile(0.75)) if len(q) else float("inf")
    df["spending_high_flag"] = df["spending_total"].fillna(0) >= threshold
    df["sleep_lt_6h_flag"] = (df["sleep_valid_flag"]) & (df["sleep_hours"] < 6)


    # aliases required by regression/exploratory naming
    for src, dst in [
        ("notes_fatigue_flag", "notes_has_fatigue"),
        ("notes_stress_flag", "notes_has_stress"),
        ("notes_achievement_flag", "notes_has_achievement"),
        ("notes_sleep_issue_flag", "notes_has_sleep_issue"),
    ]:
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src].fillna(False).astype(bool)

    quality_cols = ["notes_present_flag", "meal_logged_flag", "location_present_flag", "sleep_valid_flag"]
    df["data_quality_score"] = df[quality_cols].astype(int).mean(axis=1).round(2)
    return df


def _is_weekend(date_text: str) -> bool:
    try:
        weekday = datetime.strptime(date_text, "%Y-%m-%d").weekday()
    except Exception:
        return False
    return weekday >= 5
