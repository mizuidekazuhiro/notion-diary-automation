from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from publish.read_daily_log import DailyLogSummary
from scripts.note_batch_labeler import NoteLabel


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
    duration = _safe_float(summary.sleep_duration_min)
    all_invalid = duration is None and summary.sleep_start is None and summary.sleep_end is None
    if all_invalid:
        return False, "missing_all_sleep_fields", None
    if duration is None:
        return False, "missing_duration", None
    if duration < 0:
        return False, "negative_duration", None
    if duration == 0:
        return False, "zero_duration", None
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


def build_daily_feature_table(
    histories: Sequence[DailyLogSummary],
    note_labels: Mapping[str, NoteLabel],
) -> Any:
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
        done = item.done_count or 0
        drop = item.drop_count or 0
        denom = done + drop
        rows.append(
            {
                "date": item.target_date,
                "mood": _normalize_mood_to_score(item.mood),
                "sleep_valid_flag": sleep_valid_flag,
                "sleep_invalid_reason": sleep_invalid_reason,
                "sleep_hours": (sleep_duration / 60.0) if sleep_valid_flag and sleep_duration is not None else float("nan"),
                "bedtime_min": _to_minutes(item.sleep_start) if sleep_valid_flag else float("nan"),
                "wake_time_min": _to_minutes(item.sleep_end) if sleep_valid_flag else float("nan"),
                "sleep_score": item.sleep_score if sleep_valid_flag else float("nan"),
                "deep_sleep_minutes": item.deep_duration_min if sleep_valid_flag else float("nan"),
                "rem_duration_min": item.rem_duration_min if sleep_valid_flag else float("nan"),
                "readiness_bpm": item.readiness_bpm,
                "readiness_hrv": item.readiness_hrv,
                "baseline_hrv": item.baseline_hrv,
                "baseline_waking_bpm": item.baseline_waking_bpm,
                "spending_total": item.expenses_total,
                "task_done_count": done,
                "task_drop_count": drop,
                "task_completion_ratio": (done / denom) if denom > 0 else float("nan"),
                "kcal": item.kcal,
                "protein": item.protein,
                "fat": item.fat,
                "carb": item.carb,
                "meal_logged_flag": bool(meal_text.strip()) or any(v is not None for v in (item.kcal, item.protein, item.fat, item.carb)),
                "overeating_like_flag": bool(re.search(r"食べすぎ|食べ過ぎ|夜食|暴食", meal_text)),
                "eating_out_like_flag": bool(re.search(r"外食|レストラン|居酒屋|カフェ", meal_text)),
                "late_meal_like_flag": bool(re.search(r"夜食|深夜|遅い", meal_text)),
                "notes_sentiment_score": label.sentiment_score,
                "notes_sentiment_label": label.sentiment_label,
                "notes_fatigue_flag": label.fatigue_flag,
                "notes_stress_flag": label.stress_flag,
                "notes_social_load_flag": label.social_load_flag,
                "notes_achievement_flag": label.achievement_flag,
                "notes_self_care_flag": label.self_care_flag,
                "notes_sleep_issue_flag": label.sleep_issue_flag,
                "notes_present_flag": bool(notes_text.strip()),
                "location_present_flag": bool((item.location_summary or "").strip()),
                "transport_spend_like_flag": bool(re.search(r"交通|電車|タクシー|バス", notes_text)),
                "social_spend_like_flag": bool(re.search(r"会食|飲み会|友人|同僚", notes_text)),
                "social_event_like_flag": bool(label.social_load_flag or re.search(r"会食|飲み会|打ち合わせ|会議", notes_text)),
                "movement_intensity_like_flag": bool(re.search(r"歩いた|移動|外出|ランニング|運動", notes_text)),
                "task_balance_bad_flag": bool(denom >= 2 and (drop / denom) >= 0.6),
                **location_flags,
            }
        )
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    for col in ("sleep_hours", "sleep_score", "protein", "fat", "carb", "kcal", "spending_total", "task_done_count", "task_drop_count"):
        if col in df:
            series = pd.to_numeric(df[col], errors="coerce")
            if col.startswith("sleep_"):
                series = series.where(df["sleep_valid_flag"].fillna(False))
            baseline = series.rolling(7, min_periods=2).mean().shift(1)
            df[col] = series
            df[f"{col}_vs_7d_delta"] = series - baseline
    df["done_vs_7d_delta"] = df["task_done_count"] - df["task_done_count"].rolling(7, min_periods=2).mean().shift(1)
    df["drop_vs_7d_delta"] = df["task_drop_count"] - df["task_drop_count"].rolling(7, min_periods=2).mean().shift(1)
    df["spending_vs_7d_delta"] = df["spending_total"] - df["spending_total"].rolling(7, min_periods=2).mean().shift(1)
    df["sleep_vs_7d_delta"] = df["sleep_hours_vs_7d_delta"]
    df["sleep_score_vs_7d_delta"] = df["sleep_score_vs_7d_delta"]
    df["spending_high_flag"] = df["spending_total"].fillna(0) >= df["spending_total"].fillna(0).quantile(0.75)
    df["bedtime_after_0100_flag"] = df["bedtime_min"] >= 60
    df["sleep_lt_6h_flag"] = (df["sleep_valid_flag"]) & (df["sleep_hours"] < 6)
    df["drop_high_flag"] = df["task_drop_count"] >= 2
    df["done_low_flag"] = df["task_done_count"] <= 1
    quality_cols = ["notes_present_flag", "meal_logged_flag", "location_present_flag", "sleep_valid_flag"]
    df["data_quality_score"] = df[quality_cols].astype(int).mean(axis=1).round(2)
    df["rem_duration_min"] = df.get("rem_duration_min", np.nan)
    return df
