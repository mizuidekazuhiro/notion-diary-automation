from __future__ import annotations

from typing import Any

FEATURES = [
    "sleep_valid_flag",
    "sleep_hours",
    "bedtime_min",
    "sleep_score",
    "sleep_vs_7d_delta",
    "sleep_score_vs_7d_delta",
    "spending_total",
    "spending_vs_7d_delta",
    "task_done_count",
    "task_drop_count",
    "task_completion_ratio",
    "done_vs_7d_delta",
    "drop_vs_7d_delta",
    "kcal",
    "protein",
    "fat",
    "carb",
    "protein_vs_7d_delta",
    "fat_vs_7d_delta",
    "carb_vs_7d_delta",
    "notes_has_exercise",
    "notes_has_social",
    "notes_has_drinking",
    "notes_has_fatigue",
    "notes_has_stress",
    "notes_has_conflict",
    "notes_has_regret",
    "notes_has_productive",
    "notes_has_moderate_productivity",
    "notes_has_achievement",
    "notes_has_money_saved",
    "notes_has_diet_disruption",
    "notes_has_sleep_issue",
    "notes_has_late_work",
    "notes_has_early_home",
    "notes_has_business_trip",
    "notes_signal_count",
    "notes_avg_confidence",
    "notes_parse_quality_score",
    "notes_social_load_flag",
    "notes_recovery_like_flag",
    "notes_self_control_flag",
    "notes_work_progress_flag",
    "notes_life_disruption_flag",
]


def run_low_mood_regression(df: Any) -> dict[str, Any]:
    base = {
        "available": False,
        "sample_size": max(0, len(df)),
        "regression_target_name": "today_low_mood_flag",
        "regression_feature_names": list(FEATURES),
        "top_positive_risk_features": [],
        "top_protective_features": [],
        "skipped_reason": None,
    }
    import importlib

    sk_spec = importlib.util.find_spec("sklearn")
    if sk_spec is None:
        return {**base, "skipped_reason": "sklearn_not_installed"}
    LogisticRegression = importlib.import_module("sklearn.linear_model").LogisticRegression
    Pipeline = importlib.import_module("sklearn.pipeline").Pipeline
    StandardScaler = importlib.import_module("sklearn.preprocessing").StandardScaler

    work = df.copy().sort_values("date").reset_index(drop=True)
    work["target"] = (work["mood"].fillna(5) <= 2).astype(int)
    train = work.copy()

    if len(train) < 10:
        return {**base, "sample_size": len(train), "skipped_reason": "insufficient_samples"}
    if train["target"].nunique() < 2:
        return {**base, "sample_size": len(train), "skipped_reason": "single_class_target"}

    for col in FEATURES:
        if col not in train.columns:
            train[col] = 0.0

    for col in ("sleep_hours", "bedtime_min", "sleep_score", "sleep_vs_7d_delta", "sleep_score_vs_7d_delta"):
        if col in train:
            train.loc[~train["sleep_valid_flag"].fillna(False), col] = float("nan")

    x = train[FEATURES].copy().fillna(0.0)
    for col in x.columns:
        if x[col].dtype == bool:
            x[col] = x[col].astype(int)
    y = train["target"]

    try:
        model = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=500)),
        ])
        model.fit(x, y)
        coef = model.named_steps["clf"].coef_[0]
        pairs = sorted(zip(FEATURES, coef), key=lambda p: p[1], reverse=True)
    except Exception:
        return {**base, "sample_size": len(train), "skipped_reason": "fit_exception"}

    top_positive = [name for name, c in pairs if c > 0][:5]
    top_negative = [name for name, c in sorted(pairs, key=lambda p: p[1]) if c < 0][:5]
    return {
        "available": True,
        "sample_size": len(train),
        "regression_target_name": "today_low_mood_flag",
        "regression_feature_names": list(FEATURES),
        "top_positive_risk_features": top_positive,
        "top_protective_features": top_negative,
        "skipped_reason": None,
    }
