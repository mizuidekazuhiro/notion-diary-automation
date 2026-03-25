from __future__ import annotations

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


def build_daily_feature_table(
    histories: Sequence[DailyLogSummary],
    note_labels: Mapping[str, NoteLabel],
) -> Any:
    import importlib
    pd_spec = importlib.util.find_spec("pandas")
    if pd_spec is None:
        raise RuntimeError("pandas is required for today advice feature table")
    pd = importlib.import_module("pandas")
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
        done = item.done_count or 0
        drop = item.drop_count or 0
        denom = done + drop
        rows.append(
            {
                "date": item.target_date,
                "mood": _normalize_mood_to_score(item.mood),
                "sleep_hours": (item.sleep_duration_min / 60.0) if item.sleep_duration_min is not None else float("nan"),
                "bedtime_min": _to_minutes(item.sleep_start),
                "wake_time_min": _to_minutes(item.sleep_end),
                "sleep_score": item.sleep_score,
                "deep_sleep_minutes": item.deep_duration_min,
                "awakenings": float("nan"),
                "spending_total": item.expenses_total,
                "task_done_count": done,
                "task_drop_count": drop,
                "task_completion_ratio": (done / denom) if denom > 0 else float("nan"),
                "notes_sentiment_score": label.sentiment_score,
                "notes_sentiment_label": label.sentiment_label,
                "notes_fatigue_flag": label.fatigue_flag,
                "notes_stress_flag": label.stress_flag,
                "notes_social_load_flag": label.social_load_flag,
                "notes_achievement_flag": label.achievement_flag,
                "notes_self_care_flag": label.self_care_flag,
                "notes_sleep_issue_flag": label.sleep_issue_flag,
            }
        )
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    df["spending_high_flag"] = df["spending_total"].fillna(0) >= df["spending_total"].fillna(0).quantile(0.75)
    df["bedtime_after_0100_flag"] = df["bedtime_min"] >= 60
    df["sleep_lt_6h_flag"] = df["sleep_hours"] < 6
    df["drop_high_flag"] = df["task_drop_count"] >= 2
    df["done_low_flag"] = df["task_done_count"] <= 1
    return df
