from __future__ import annotations

from itertools import combinations
from typing import Any

LEAKAGE_COLUMNS = {
    "target",
    "today_low_mood_flag",
    "today_fatigue_flag",
    "today_low_productivity_flag",
}


def _feature_columns(df: Any) -> list[str]:
    excluded = {"date", "mood", "sleep_invalid_reason", *LEAKAGE_COLUMNS}
    return [c for c in df.columns if c not in excluded]


def analyze_exploratory_patterns(df: Any) -> dict[str, Any]:
    work = df.copy().sort_values("date").reset_index(drop=True)
    work["today_low_mood_flag"] = (work["mood"].fillna(5) <= 2).astype(int)
    work["today_fatigue_flag"] = work["notes_fatigue_flag"].fillna(False).astype(int)
    work["today_low_productivity_flag"] = (
        (work["task_drop_count"].fillna(0) >= 2) | (work["task_done_count"].fillna(0) <= 1)
    ).astype(int)
    train = work.copy()
    if len(train) == 0:
        return {
            "exploratory_target_name": "today_low_mood_flag",
            "univariate_summary": [],
            "top_single_features_for_low_mood": [],
            "top_protective_features": [],
            "top_combination_patterns_for_low_mood": [],
            "top_combination_patterns_for_high_mood": [],
            "matched_today_conditions": [],
            "matched_patterns_count": 0,
            "evidence_used": [],
            "reason_codes": ["insufficient_history"],
        }

    feature_cols = _feature_columns(train)
    for col in feature_cols:
        if train[col].dtype == bool:
            train[col] = train[col].astype(int)
    target = train["today_low_mood_flag"].astype(int)
    baseline = float(target.mean()) if len(target) else 0.0

    univariate_summary: list[dict[str, Any]] = []
    risk: list[dict[str, Any]] = []
    protective: list[dict[str, Any]] = []
    for col in feature_cols:
        series = train[col]
        if series.dtype == object and col != "notes_sentiment_label":
            continue
        if col == "notes_sentiment_label":
            series = series.map({"positive": 1, "neutral": 0, "negative": -1})
        else:
            series = series.astype(float) if str(series.dtype) != "bool" else series.astype(int)
        valid = series.notna()
        if int(valid.sum()) < 5:
            continue
        low = series[target == 1]
        mid = series[(train["mood"] == 3)]
        high = series[(train["mood"] >= 4)]
        low_mean = float(low.mean()) if len(low) else 0.0
        high_mean = float(high.mean()) if len(high) else 0.0
        delta = round(low_mean - high_mean, 3)
        corr = float(series.fillna(0).corr(target)) if series.nunique(dropna=True) > 1 else 0.0
        univariate_summary.append(
            {
                "feature": col,
                "count": int(valid.sum()),
                "support": int(valid.sum()),
                "mean": round(float(series.fillna(0).mean()), 3),
                "median": round(float(series.dropna().median()), 3) if valid.any() else None,
                "low_mood_mean": round(low_mean, 3),
                "middle_mood_mean": round(float(mid.mean()), 3) if len(mid) else 0.0,
                "high_mood_mean": round(high_mean, 3),
                "delta": delta,
                "correlation": round(corr, 3),
            }
        )
        item = {"feature": col, "delta": delta, "correlation": round(corr, 3), "support": int(valid.sum())}
        if delta > 0:
            risk.append(item)
        elif delta < 0:
            protective.append(item)

    risk = sorted(risk, key=lambda x: (x["delta"], abs(x["correlation"])), reverse=True)[:8]
    protective = sorted(protective, key=lambda x: (x["delta"], -abs(x["correlation"])))[:8]

    bool_features = [c for c in feature_cols if train[c].dropna().isin([0, 1, True, False]).all()]
    combination_risk: list[dict[str, Any]] = []
    combination_good: list[dict[str, Any]] = []
    for f1, f2 in combinations(bool_features[:16], 2):
        mask = train[f1].fillna(False).astype(bool) & train[f2].fillna(False).astype(bool)
        sample = int(mask.sum())
        if sample < 3:
            continue
        hit = float(target[mask].mean())
        item = {
            "features": [f1, f2],
            "sample_size": sample,
            "hit_rate": round(hit, 3),
            "baseline_rate": round(baseline, 3),
            "delta": round(hit - baseline, 3),
        }
        if hit >= baseline:
            combination_risk.append(item)
        else:
            combination_good.append(item)

    combination_risk = sorted(combination_risk, key=lambda x: (x["delta"], x["sample_size"]), reverse=True)[:5]
    combination_good = sorted(combination_good, key=lambda x: (x["delta"], -x["sample_size"]))[:5]

    today = work.iloc[-1]
    matched: list[dict[str, Any]] = []
    for item in combination_risk:
        f1, f2 = item["features"]
        if bool(today.get(f1, False)) and bool(today.get(f2, False)):
            matched.append(item)

    evidence = []
    if risk:
        evidence.append({"source_type": "univariate", "feature": risk[0]["feature"], "delta": risk[0]["delta"]})
    if matched:
        evidence.append({"source_type": "combination", "features": matched[0]["features"], "delta": matched[0]["delta"]})

    return {
        "exploratory_target_name": "today_low_mood_flag",
        "univariate_summary": univariate_summary,
        "top_single_features_for_low_mood": risk,
        "top_protective_features": protective,
        "top_combination_patterns_for_low_mood": combination_risk,
        "top_combination_patterns_for_high_mood": combination_good,
        "matched_today_conditions": matched,
        "matched_patterns_count": len(matched),
        "evidence_used": evidence,
        "reason_codes": [] if (risk or matched) else ["no_clear_pattern"],
    }
