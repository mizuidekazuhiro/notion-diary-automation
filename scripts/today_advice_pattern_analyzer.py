from __future__ import annotations

from itertools import combinations
from typing import Any, Mapping

from scripts.advice_evidence import MIN_GROUP_SUPPORT, is_quality_feature, observed_targets

LEAKAGE_COLUMNS = {"target", "today_low_mood_flag", "today_fatigue_flag", "today_low_productivity_flag"}


def _feature_columns(df: Any) -> list[str]:
    excluded = {"date", "mood", "sleep_invalid_reason", *LEAKAGE_COLUMNS}
    return [c for c in df.columns if c not in excluded and not is_quality_feature(c)
            and not c.startswith("next_day")]


def analyze_exploratory_patterns(
    df: Any, *, today_features: Mapping[str, Any] | None = None, today_feature_date: str | None = None,
) -> dict[str, Any]:
    import pandas as pd
    train, counts, reason = observed_targets(df.sort_values("date"))
    result: dict[str, Any] = {
        "exploratory_target_name": "observed_same_day_low_mood", "scope": "exploratory_association",
        "target_counts": counts, "univariate_summary": [], "top_single_features_for_low_mood": [],
        "top_protective_features": [], "top_combination_patterns_for_low_mood": [],
        "top_combination_patterns_for_high_mood": [], "matched_today_conditions": [],
        "matched_patterns_count": 0, "evidence_used": [], "match_source_date": None,
        "reason_codes": [reason] if reason else [],
    }
    if reason:
        return result

    numeric = {c: pd.to_numeric(train[c], errors="coerce") for c in _feature_columns(train)}
    for col, series in numeric.items():
        valid = series.notna()
        low = series[valid & (train["target"] == 1)]
        other = series[valid & (train["target"] == 0)]
        if min(len(low), len(other)) < MIN_GROUP_SUPPORT or series.nunique(dropna=True) < 2:
            continue
        delta = float(low.mean() - other.mean())
        corr = float(series[valid].corr(train.loc[valid, "target"]))
        item = {"feature": col, "delta": round(delta, 3), "correlation": round(corr, 3),
                "support": int(valid.sum()), "positive_support": len(low), "comparison_support": len(other)}
        result["univariate_summary"].append({**item, "mean": float(series[valid].mean()),
            "median": float(series[valid].median()), "count": int(valid.sum()),
            "low_mood_mean": float(low.mean()), "high_mood_mean": float(other.mean())})
        if item["delta"] > 0:
            result["top_single_features_for_low_mood"].append(item)
        elif item["delta"] < 0:
            result["top_protective_features"].append(item)

    bool_features = [c for c, s in numeric.items() if s.notna().any() and s.dropna().isin([0, 1]).all()]
    seen_supports: set[tuple[Any, ...]] = set()
    for f1, f2 in combinations(bool_features, 2):
        valid = numeric[f1].notna() & numeric[f2].notna()
        mask = valid & numeric[f1].eq(1) & numeric[f2].eq(1)
        control = valid & ~mask
        if min(int(mask.sum()), int(control.sum())) < MIN_GROUP_SUPPORT:
            continue
        support_key = (tuple(train.index[mask]), tuple(train.index[control]))
        if support_key in seen_supports:
            continue
        seen_supports.add(support_key)
        hit = float(train.loc[mask, "target"].mean())
        baseline = float(train.loc[valid, "target"].mean())
        delta = round(hit - baseline, 3)
        if delta == 0:
            continue
        item = {"features": [f1, f2], "sample_size": int(mask.sum()),
                "comparison_size": int(control.sum()), "hit_rate": round(hit, 3),
                "baseline_rate": round(baseline, 3), "comparison_rate": float(train.loc[control, "target"].mean()),
                "delta": delta, "scope": "exploratory_association"}
        key = "top_combination_patterns_for_low_mood" if delta > 0 else "top_combination_patterns_for_high_mood"
        result[key].append(item)

    for key in ("top_single_features_for_low_mood", "top_protective_features",
                "top_combination_patterns_for_low_mood", "top_combination_patterns_for_high_mood"):
        result[key] = sorted(result[key], key=lambda x: abs(x["delta"]), reverse=True)[:5]
    # A historical row is never a substitute for a target-day observation.
    if today_features is not None and today_feature_date:
        result["match_source_date"] = today_feature_date
        for item in result["top_combination_patterns_for_low_mood"]:
            values = [today_features.get(f) for f in item["features"]]
            if all(pd.notna(v) and v == 1 for v in values):
                result["matched_today_conditions"].append(item)
    result["matched_patterns_count"] = len(result["matched_today_conditions"])
    for item in result["top_single_features_for_low_mood"][:1]:
        result["evidence_used"].append({"source_type": "historical_association", **item})
    if not result["evidence_used"]:
        result["reason_codes"] = ["no_clear_pattern"]
    return result
