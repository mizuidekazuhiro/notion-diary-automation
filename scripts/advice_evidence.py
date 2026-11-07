"""Eligibility for exploratory associations, never a validated personal forecast."""
from __future__ import annotations

from typing import Any

# Retain the strictest existing model minimum (12) and pair support (3).
# Also require both outcome classes and an independent comparison group.
MIN_VALID_TARGETS = 12
MIN_CLASS_SUPPORT = 3
MIN_GROUP_SUPPORT = 3
EVIDENCE_POLICY_VERSION = "observed-outcomes-v2"

QUALITY_FEATURES = {
    "sentiment_unknown", "no_signal_note", "parse_low_confidence", "tag_extract_failed",
    "notes_present_flag", "notes_signal_count", "notes_positive_signal_count",
    "notes_negative_signal_count", "notes_behavior_signal_count", "notes_state_signal_count",
    "notes_avg_confidence", "notes_parse_quality_score", "data_quality_score",
    "sleep_valid_flag", "meal_logged_flag", "location_present_flag", "study_logged_flag",
    "weather_retrieved_flag", "schedule_signal_available_flag", "forbidden_inputs_used",
}


def is_quality_feature(name: str) -> bool:
    return any(name == key or name.startswith(key + "_") for key in QUALITY_FEATURES) or any(
        part in name for part in ("missing", "invalid", "parse_quality", "confidence", "available")
    )


def observed_targets(df: Any) -> tuple[Any, dict[str, int], str | None]:
    import pandas as pd
    # Repeated pages/dates are not independent observations. Conflicting copies
    # cannot establish an outcome or exposure and are excluded altogether.
    if "date" in df and df["date"].duplicated().any():
        conflicting = [day for day, group in df.groupby("date")
                       if len(group.drop_duplicates()) > 1]
        df = df[~df["date"].isin(conflicting)].drop_duplicates("date")
    mood = pd.to_numeric(df["mood"], errors="coerce")
    valid = mood.between(1, 5)
    train = df.loc[valid].copy().reset_index(drop=True)
    train["target"] = (mood[valid].reset_index(drop=True) <= 2).astype(int)
    positive = int(train["target"].sum())
    negative = len(train) - positive
    counts = {"valid": len(train), "missing": len(df) - len(train), "positive": positive, "negative": negative}
    reason = None
    if len(train) < MIN_VALID_TARGETS:
        reason = "insufficient_valid_targets"
    elif min(positive, negative) < MIN_CLASS_SUPPORT:
        reason = "insufficient_class_support"
    return train, counts, reason
