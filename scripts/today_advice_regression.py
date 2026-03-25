from __future__ import annotations

from typing import Any


FEATURES = [
    "sleep_hours",
    "bedtime_min",
    "sleep_score",
    "spending_total",
    "task_done_count",
    "task_drop_count",
    "notes_sentiment_score",
    "notes_fatigue_flag",
    "notes_stress_flag",
    "notes_social_load_flag",
    "notes_achievement_flag",
    "notes_self_care_flag",
    "notes_sleep_issue_flag",
]


def run_low_mood_regression(df: Any) -> dict[str, Any]:
    import importlib
    sk_spec = importlib.util.find_spec("sklearn")
    if sk_spec is None:
        return {"available": False, "sample_size": max(0, len(df) - 1), "top_positive_risk_features": [], "top_protective_features": []}
    LogisticRegression = importlib.import_module("sklearn.linear_model").LogisticRegression
    Pipeline = importlib.import_module("sklearn.pipeline").Pipeline
    StandardScaler = importlib.import_module("sklearn.preprocessing").StandardScaler
    work = df.copy().sort_values("date").reset_index(drop=True)
    work["target"] = (work["mood"].shift(-1).fillna(5) <= 2).astype(int)
    train = work.iloc[:-1].copy()
    if len(train) < 8 or train["target"].nunique() < 2:
        return {"available": False, "sample_size": len(train), "top_positive_risk_features": [], "top_protective_features": []}
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
        return {"available": False, "sample_size": len(train), "top_positive_risk_features": [], "top_protective_features": []}
    return {
        "available": True,
        "sample_size": len(train),
        "top_positive_risk_features": [name for name, c in pairs if c > 0][:3],
        "top_protective_features": [name for name, c in sorted(pairs, key=lambda p: p[1]) if c < 0][:3],
    }
