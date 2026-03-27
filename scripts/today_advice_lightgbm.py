from __future__ import annotations

from typing import Any

LEAKAGE_COLUMNS = {
    "date",
    "mood",
    "sleep_invalid_reason",
    "next_day_mood_score",
    "next_day_low_mood_flag",
    "next_day_fatigue_flag",
    "next_day_low_productivity_flag",
    "target",
}


def run_lightgbm_low_mood(df: Any) -> dict[str, Any]:
    base = {
        "available": False,
        "sample_size": max(0, len(df) - 1),
        "feature_importances": [],
        "top_risk_features": [],
        "top_protective_features": [],
        "prediction_probability_for_today": None,
        "today_contribution_features": [],
        "skipped_reason": None,
        "skipped_columns": [],
        "feature_columns": [],
    }

    import importlib
    import numpy as np

    if importlib.util.find_spec("lightgbm") is None:
        return {**base, "skipped_reason": "lightgbm_not_installed"}
    LGBMClassifier = importlib.import_module("lightgbm").LGBMClassifier

    work = df.copy().sort_values("date").reset_index(drop=True)
    work["target"] = (work["mood"].shift(-1).fillna(5) <= 2).astype(int)
    train = work.iloc[:-1].copy()
    if len(train) < 12:
        return {**base, "sample_size": len(train), "skipped_reason": "insufficient_samples"}
    if train["target"].nunique() < 2:
        return {**base, "sample_size": len(train), "skipped_reason": "single_class_target"}

    feature_cols = [c for c in train.columns if c not in LEAKAGE_COLUMNS]
    x = train[feature_cols].copy()
    unsupported_columns: list[str] = []
    for col in list(x.columns):
        if x[col].dtype == bool:
            x[col] = x[col].astype(int)
        elif str(x[col].dtype) == "object":
            if col == "notes_sentiment_label":
                x[col] = x[col].map({"positive": 1, "neutral": 0, "negative": -1}).fillna(0).astype(int)
            else:
                unsupported_columns.append(col)
    if unsupported_columns:
        x = x.drop(columns=unsupported_columns)
    x = x.select_dtypes(include=["number", "bool"]).copy()
    for col in x.columns:
        if x[col].dtype == bool:
            x[col] = x[col].astype(int)
    feature_cols = list(x.columns)
    if not feature_cols:
        return {**base, "sample_size": len(train), "skipped_reason": "unsupported_dtype", "skipped_columns": unsupported_columns}

    missing_rate = float(x.isna().mean().mean()) if len(x.columns) else 1.0
    if missing_rate > 0.8:
        return {**base, "sample_size": len(train), "skipped_reason": "too_many_missing_values", "skipped_columns": unsupported_columns, "feature_columns": feature_cols}

    y = train["target"].astype(int)
    try:
        model = LGBMClassifier(
            n_estimators=100,
            learning_rate=0.05,
            num_leaves=15,
            random_state=42,
            verbose=-1,
        )
        model.fit(x, y)
    except Exception:
        return {**base, "sample_size": len(train), "skipped_reason": "fit_exception", "skipped_columns": unsupported_columns, "feature_columns": feature_cols}

    importances = model.feature_importances_
    fi = sorted(zip(feature_cols, importances), key=lambda p: p[1], reverse=True)
    top_importances = [{"feature": f, "importance": float(i)} for f, i in fi[:12] if i > 0]

    risk, protective = [], []
    for feature, _ in fi[:20]:
        if feature not in x.columns:
            continue
        corr = float(np.corrcoef(x[feature].fillna(0), y)[0, 1]) if x[feature].fillna(0).nunique() > 1 else 0.0
        item = {"feature": feature, "correlation": round(corr, 3)}
        if corr >= 0:
            risk.append(item)
        else:
            protective.append(item)

    today_prob = None
    today_contrib: list[dict[str, Any]] = []
    try:
        today_x = work.iloc[[-1]][feature_cols].copy()
        for col in today_x.columns:
            if today_x[col].dtype == bool:
                today_x[col] = today_x[col].astype(int)
            elif str(today_x[col].dtype) == "object" and col == "notes_sentiment_label":
                today_x[col] = today_x[col].map({"positive": 1, "neutral": 0, "negative": -1})
        today_prob = float(model.predict_proba(today_x)[0][1])
        if hasattr(model, "predict"):
            contrib_proxy = []
            for f, imp in fi[:8]:
                val = today_x.iloc[0][f]
                contrib_proxy.append({"feature": f, "today_value": None if val != val else float(val), "importance": float(imp)})
            today_contrib = contrib_proxy
    except Exception:
        today_prob = None

    return {
        "available": True,
        "sample_size": len(train),
        "feature_importances": top_importances,
        "top_risk_features": risk[:8],
        "top_protective_features": protective[:8],
        "prediction_probability_for_today": today_prob,
        "today_contribution_features": today_contrib,
        "skipped_reason": None,
        "skipped_columns": unsupported_columns,
        "feature_columns": feature_cols,
    }
