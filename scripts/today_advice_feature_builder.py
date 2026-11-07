from __future__ import annotations

import re
import os
import json
from datetime import datetime
from typing import Any, Mapping, Sequence

from publish.read_daily_log import DailyLogSummary
from scripts.note_batch_labeler import NoteLabel
from scripts.sleep_utils import resolve_sleep_duration_minutes, resolve_sleep_target_date
from scripts.data_status import metric_available, source_states, UNAVAILABLE
from scripts.location_features import extract_location_flags

FORBIDDEN_GPT_FIELDS = {
    "diary",
    "sleep_analysis_jp",
    "today_condition_forecast_jp",
    "today_advice",
    "f_risk_alert",
}


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
    if source_states(summary).get("sleep", {}).get("status") in UNAVAILABLE:
        return False, "sleep_source_unavailable", None
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
    return extract_location_flags(text)


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
        schedule_total = len(item.done_tasks_detail or [])
        same_day_events = 0
        future_events = 0
        for task in item.done_tasks_detail or []:
            event_date = (task.event_date or "").strip()
            if not event_date:
                continue
            if event_date == item.target_date:
                same_day_events += 1
            elif event_date > item.target_date:
                future_events += 1

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
                "low_protein_flag": bool(_safe_float(item.protein) is not None and float(item.protein) < 55.0),
                "high_fat_flag": bool(_safe_float(item.fat) is not None and float(item.fat) > 75.0),
                "high_carb_flag": bool(_safe_float(item.carb) is not None and float(item.carb) > 320.0),
                "spending_total": _safe_float(item.expenses_total),
                "study_minutes": _safe_float(getattr(item, "study_minutes", None)),
                "study_sessions": _safe_float(getattr(item, "study_sessions", None)),
                "study_last_used_at": str(getattr(item, "study_last_used_at", "") or ""),
                "study_logged_flag": bool((_safe_float(getattr(item, "study_minutes", None)) or 0) > 0),
                "workout_done_flag": bool(getattr(item, "workout_done", False)),
                "workout_sessions": _safe_float(getattr(item, "workout_sessions", None)),
                "workout_duration_min": _safe_float(getattr(item, "workout_duration_min", None)),
                "workout_sets": _safe_float(getattr(item, "workout_sets", None)),
                "workout_volume_kg": _safe_float(getattr(item, "workout_volume_kg", None)),
                "workout_calories": _safe_float(getattr(item, "workout_calories", None)),
                "expense_f_count": _safe_float(item.expense_f_count),
                "expense_f_total": _safe_float(item.expense_f_total),
                "transport_spend_like_flag": bool(re.search(r"交通|電車|タクシー|バス", notes_text + activity_text)),
                "social_spend_like_flag": bool(re.search(r"会食|飲み会|友人|同僚", notes_text + activity_text)),
                "convenience_store_like_flag": bool(re.search(r"コンビニ|セブン|ファミマ|ローソン", notes_text + activity_text + meal_text)),
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
                "weather_bad_flag": (
                    bool((_safe_float(item.weather_precip_probability_max) or 0) >= 60 or (_safe_float(item.weather_code) or 0) >= 60)
                    if item.weather_precip_probability_max is not None or item.weather_code is not None else np.nan
                ),
                "weather_temp_range_c": (
                    (_safe_float(item.weather_temp_max_c) - _safe_float(item.weather_temp_min_c))
                    if _safe_float(item.weather_temp_max_c) is not None and _safe_float(item.weather_temp_min_c) is not None
                    else np.nan
                ),
                "weather_retrieved_flag": bool(
                    _safe_float(item.weather_code) is not None
                    or _safe_float(item.weather_precip_probability_max) is not None
                    or _safe_float(item.weather_temp_max_c) is not None
                ),
                "schedule_signal_available_flag": bool(schedule_total > 0),
                "schedule_same_day_event_count": same_day_events,
                "schedule_future_event_count": future_events,
                "late_event_like_flag": bool(any((task.event_date or "").strip() > item.target_date for task in item.done_tasks_detail or [])),
                "is_weekend": _is_weekend(item.target_date),
                **location_flags,
            }
        )

    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    if df.empty:
        return df
    for col in df.select_dtypes(include=["bool"]).columns:
        if col not in {"sleep_valid_flag", "notes_present_flag", "meal_logged_flag", "location_present_flag"}:
            df[col] = df[col].astype(float)
    # Absence/parse failure is not a negative behavioral observation.
    for index, item in enumerate(sorted(histories, key=lambda value: value.target_date)):
        label = note_labels.get(item.target_date)
        notes_known = bool((item.notes or "").strip()) and label is not None and not label.tag_extract_failed
        if not notes_known:
            for col in df.columns:
                if col.startswith("notes_") and col not in {"notes_present_flag", "notes_parse_quality_score"}:
                    df.at[index, col] = np.nan
        if source_states(item).get("nutrition", {}).get("status") in UNAVAILABLE:
            for col in ("kcal", "protein", "fat", "carb", "low_protein_flag", "high_fat_flag", "high_carb_flag"):
                df.at[index, col] = np.nan
        try:
            weather_date = json.loads(item.generation_metadata or "{}").get("weather", {}).get("target_date")
        except (ValueError, TypeError, AttributeError):
            weather_date = None
        if weather_date != item.target_date:
            for col in ("weather_bad_flag", "weather_code", "weather_temp_max_c", "weather_temp_min_c", "weather_precip_probability_max", "weather_temp_range_c"):
                df.at[index, col] = np.nan
        if not ((item.notes or "").strip() or (item.activity_summary or "").strip()):
            for col in ("transport_spend_like_flag", "social_spend_like_flag", "social_event_like_flag", "movement_intensity_like_flag"):
                df.at[index, col] = np.nan
        if not ((item.notes or "").strip() or (item.activity_summary or "").strip() or (item.meal_summary or "").strip()):
            df.at[index, "convenience_store_like_flag"] = np.nan
        for col in location_flags:
            if not (item.location_summary or "").strip():
                df.at[index, col] = np.nan
        for field, raw in (("task_done_count", item.done_count), ("task_drop_count", item.drop_count)):
            if raw is None:
                df.at[index, field] = np.nan
        if item.done_count is None:
            df.at[index, "done_low_flag"] = np.nan
        if item.drop_count is None:
            df.at[index, "drop_high_flag"] = np.nan
        if item.done_count is None or item.drop_count is None:
            df.at[index, "task_completion_ratio"] = np.nan
            df.at[index, "task_balance_bad_flag"] = np.nan
        if getattr(item, "workout_done", None) is None or not metric_available(item, "workout", getattr(item, "workout_duration_min", None)):
            df.at[index, "workout_done_flag"] = np.nan
            for col in ("workout_sessions", "workout_duration_min", "workout_sets", "workout_volume_kg", "workout_calories"):
                df.at[index, col] = np.nan
        if not metric_available(item, "study", getattr(item, "study_minutes", None)):
            df.at[index, "study_logged_flag"] = np.nan
            df.at[index, "study_minutes"] = np.nan
            df.at[index, "study_sessions"] = np.nan
        for col, raw in (("low_protein_flag", item.protein), ("high_fat_flag", item.fat), ("high_carb_flag", item.carb)):
            if raw is None:
                df.at[index, col] = np.nan
        if not (item.meal_summary or "").strip() and all(v is None for v in (item.kcal, item.protein, item.fat, item.carb)):
            for col in ("overeating_like_flag", "eating_out_like_flag", "late_meal_like_flag"):
                df.at[index, col] = np.nan
    # Calendar days, rather than adjacent recorded rows, define lag and windows.
    if not df["date"].duplicated().any():
        calendar = pd.date_range(df["date"].min(), df["date"].max()).strftime("%Y-%m-%d")
        df = df.set_index("date").reindex(calendar).rename_axis("date").reset_index()
        for col in ("sleep_valid_flag", "notes_present_flag", "meal_logged_flag", "location_present_flag"):
            df[col] = df[col].eq(True)
    numeric_targets = [
        "sleep_hours", "sleep_score", "kcal", "protein", "fat", "carb", "spending_total",
        "task_done_count", "task_drop_count", "workout_sessions", "workout_duration_min",
        "workout_sets", "workout_volume_kg", "workout_calories"
    ]
    numeric_updates: dict[str, Any] = {}
    derived_cols: dict[str, Any] = {}
    for col in numeric_targets:
        if col not in df:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        if col.startswith("sleep"):
            series = series.where(df["sleep_valid_flag"].fillna(False))
        baseline = series.rolling(7, min_periods=2).mean().shift(1)
        numeric_updates[col] = series
        derived_cols[f"{col}_vs_7d_delta"] = series - baseline

    derived_cols["done_vs_7d_delta"] = df["task_done_count"] - df["task_done_count"].rolling(7, min_periods=2).mean().shift(1)
    derived_cols["drop_vs_7d_delta"] = df["task_drop_count"] - df["task_drop_count"].rolling(7, min_periods=2).mean().shift(1)
    derived_cols["spending_vs_7d_delta"] = df["spending_total"] - df["spending_total"].rolling(7, min_periods=2).mean().shift(1)
    derived_cols["sleep_vs_7d_delta"] = derived_cols.get("sleep_hours_vs_7d_delta", np.nan)
    derived_cols["sleep_score_vs_7d_delta"] = derived_cols.get("sleep_score_vs_7d_delta", np.nan)
    derived_cols["workout_duration_vs_7d_delta"] = derived_cols.get("workout_duration_min_vs_7d_delta", np.nan)
    derived_cols["workout_volume_vs_7d_delta"] = derived_cols.get("workout_volume_kg_vs_7d_delta", np.nan)
    df = df.assign(**numeric_updates, **derived_cols)

    q = df["spending_total"].dropna()
    threshold = float(q.quantile(0.75)) if len(q) else float("inf")
    df["spending_high_flag"] = (df["spending_total"] >= threshold).astype(float).where(df["spending_total"].notna())
    df["sleep_lt_6h_flag"] = (df["sleep_hours"] < 6).astype(float).where(df["sleep_valid_flag"] & df["sleep_hours"].notna())


    # aliases required by regression/exploratory naming
    alias_cols: dict[str, Any] = {}
    for src, dst in [
        ("notes_fatigue_flag", "notes_has_fatigue"),
        ("notes_stress_flag", "notes_has_stress"),
        ("notes_achievement_flag", "notes_has_achievement"),
        ("notes_sleep_issue_flag", "notes_has_sleep_issue"),
    ]:
        if src in df.columns and dst not in df.columns:
            alias_cols[dst] = pd.to_numeric(df[src], errors="coerce")
    if alias_cols:
        df = df.assign(**alias_cols)

    quality_cols = ["notes_present_flag", "meal_logged_flag", "location_present_flag", "sleep_valid_flag"]
    df["data_quality_score"] = df[quality_cols].astype(int).mean(axis=1).round(2)
    df = df.copy()
    df = _add_temporal_features(df=df, np=np)
    return df.assign(forbidden_inputs_used=False, forbidden_inputs_used_detail="")


def _is_weekend(date_text: str) -> bool:
    try:
        weekday = datetime.strptime(date_text, "%Y-%m-%d").weekday()
    except Exception:
        return False
    return weekday >= 5


def _add_temporal_features(*, df: Any, np: Any) -> Any:
    import importlib
    pd = importlib.import_module("pandas")
    lag_periods = (1, 2, 3, 5, 7, 14)
    roll_windows = (3, 5, 7, 14)
    lag_targets = [
        "sleep_hours",
        "spending_total",
        "task_done_count",
        "task_drop_count",
        "notes_stress_flag",
        "workout_done_flag",
        "workout_duration_min",
        "workout_volume_kg",
        "notes_fatigue_flag",
        "notes_social_load_flag",
        "notes_has_late_work",
        "notes_has_exercise",
        "carb",
        "study_minutes",
        "study_sessions",
        "study_logged_flag",
    ]
    derived_cols: dict[str, Any] = {}
    for col in lag_targets:
        if col not in df.columns:
            continue
        series = df[col]
        if series.dtype == bool:
            series = series.astype(int)
        for lag in lag_periods:
            derived_cols[f"{col}_lag_{lag}"] = series.shift(lag)
        for w in roll_windows:
            derived_cols[f"{col}_rolling_sum_{w}d"] = series.rolling(w, min_periods=1).sum().shift(1)
            derived_cols[f"{col}_rolling_mean_{w}d"] = series.rolling(w, min_periods=1).mean().shift(1)

    derived_cols["sleep_short_flag"] = (df["sleep_hours"] < 6).astype(float).where(df["sleep_valid_flag"].fillna(False))
    derived_cols["social_load_flag"] = pd.to_numeric(df["notes_social_load_flag"], errors="coerce")
    derived_cols["late_work_flag"] = pd.to_numeric(df["notes_has_late_work"], errors="coerce") if "notes_has_late_work" in df else np.nan
    derived_cols["exercise_flag"] = pd.to_numeric(df["notes_has_exercise"], errors="coerce") if "notes_has_exercise" in df else np.nan
    if "study_minutes" in df.columns:
        study_series = pd.to_numeric(df["study_minutes"], errors="coerce")
        study_baseline_7d = study_series.rolling(7, min_periods=2).mean().shift(1)
        derived_cols["study_minutes_vs_7d_delta"] = study_series - study_baseline_7d
        target_minutes_raw = os.getenv("F_RISK_STUDY_TARGET_MINUTES_PER_DAY", "").strip()
        target_minutes = float(target_minutes_raw) if target_minutes_raw else None
        heavy_threshold = float(os.getenv("F_RISK_STUDY_HEAVY_DAY_MINUTES", "180") or "180")
        derived_cols["study_under_target_flag"] = (
            (study_series < target_minutes).astype(float).where(study_series.notna()) if target_minutes is not None else pd.Series(np.nan, index=df.index)
        )
        derived_cols["study_heavy_day_flag"] = ((study_series >= heavy_threshold) | (study_series >= (study_baseline_7d + 90))).astype(float).where(study_series.notna())
        logged = (study_series > 0).astype(float).where(study_series.notna())
        derived_cols["study_consistency_score_7d"] = logged.rolling(7, min_periods=1).mean().shift(1)
        zero_streak = []
        streak = 0
        for value in logged.tolist():
            if pd.isna(value):
                streak = 0
                zero_streak.append(np.nan)
                continue
            if value == 0:
                streak += 1
            else:
                streak = 0
            zero_streak.append(streak)
        derived_cols["study_zero_day_streak"] = zero_streak
    df = pd.concat([df, pd.DataFrame(derived_cols, index=df.index)], axis=1).copy()
    for col in ("study_zero_day_streak", "study_heavy_day_flag", "study_under_target_flag", "study_minutes_vs_7d_delta"):
        if col in df.columns:
            df[f"{col}_lag_1"] = pd.to_numeric(df[col], errors="coerce").shift(1)

    streak_cols: dict[str, Any] = {}
    for src, out in [
        ("social_load_flag", "social_load_streak"),
        ("sleep_short_flag", "sleep_short_streak"),
        ("late_work_flag", "late_work_streak"),
        ("exercise_flag", "exercise_streak"),
    ]:
        streak_cols[out] = _streak_count(df[src])
    interaction_cols = {
        "sleep_short_x_social_load": df["sleep_short_flag"] * df["social_load_flag"],
        "fatigue_x_spending_spike": df["notes_fatigue_flag"] * (df["spending_vs_7d_delta"] > 0).astype(float).where(df["spending_vs_7d_delta"].notna()),
        "stress_x_late_work": df["notes_stress_flag"] * df["late_work_flag"],
        "drinking_x_low_sleep": df["notes_has_drinking"] * df["sleep_short_flag"],
        "high_carb_x_low_sleep": (df["carb_vs_7d_delta"] > 0).astype(float).where(df["carb_vs_7d_delta"].notna()) * df["sleep_short_flag"],
        "exercise_x_recovery": df["exercise_flag"] * df["notes_recovery_like_flag"],
    }
    df = pd.concat([df, pd.DataFrame({**streak_cols, **interaction_cols}, index=df.index)], axis=1).copy()
    numeric_cols = df.select_dtypes(include=["number"]).columns
    df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)
    return df


def _streak_count(series: Any) -> list[float]:
    import pandas as pd
    streak = 0
    out: list[int] = []
    for value in series.tolist():
        if pd.isna(value):
            streak = 0
            out.append(float("nan"))
            continue
        if value:
            streak += 1
        else:
            streak = 0
        out.append(streak)
    return out
