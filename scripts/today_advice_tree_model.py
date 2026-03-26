from __future__ import annotations

from typing import Any


def run_tree_model_summary(df: Any) -> dict[str, Any]:
    base = {
        "available": False,
        "sample_size": max(0, len(df) - 1),
        "top_feature_importances": [],
        "representative_branches": [],
        "skipped_reason": None,
    }
    import importlib

    sk_spec = importlib.util.find_spec("sklearn")
    if sk_spec is None:
        return {**base, "skipped_reason": "sklearn_not_installed"}
    DecisionTreeClassifier = importlib.import_module("sklearn.tree").DecisionTreeClassifier
    export_text = importlib.import_module("sklearn.tree").export_text
    work = df.copy().sort_values("date").reset_index(drop=True)
    work["target"] = (work["mood"].shift(-1).fillna(5) <= 2).astype(int)
    train = work.iloc[:-1].copy()
    if len(train) < 10 or train["target"].nunique() < 2:
        return {**base, "sample_size": len(train), "skipped_reason": "insufficient_samples_or_single_class"}
    feature_cols = [c for c in train.columns if c not in {"date", "mood", "sleep_invalid_reason", "target"}]
    for col in feature_cols:
        if train[col].dtype == bool:
            train[col] = train[col].astype(int)
    x = train[feature_cols].fillna(0.0)
    y = train["target"]
    try:
        model = DecisionTreeClassifier(max_depth=3, min_samples_leaf=2, random_state=42)
        model.fit(x, y)
    except Exception:
        return {**base, "sample_size": len(train), "skipped_reason": "tree_fit_failed"}
    importances = sorted(zip(feature_cols, model.feature_importances_), key=lambda p: p[1], reverse=True)
    top = [{"feature": name, "importance": round(float(score), 4)} for name, score in importances[:8] if score > 0]
    tree_text = export_text(model, feature_names=feature_cols)
    branches = [line.strip() for line in tree_text.splitlines() if "class:" in line][:5]
    return {
        "available": True,
        "sample_size": len(train),
        "top_feature_importances": top,
        "representative_branches": branches,
        "skipped_reason": None,
    }
