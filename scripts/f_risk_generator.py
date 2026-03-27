from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from publish.read_daily_log import DailyLogSummary, read_daily_log
from scripts.note_batch_labeler import label_notes_in_batches
from scripts.today_advice_feature_builder import build_daily_feature_table


@dataclass(frozen=True)
class FRiskResult:
    alert_text: Optional[str]
    score: Optional[float]
    reason: Optional[str]
    matched_patterns: list[str]
    skip_reason: Optional[str]
    debug_summary: dict[str, Any]


def generate_f_risk(*, daily_log_read_url: str, bearer_token: Optional[str], target_date: str) -> FRiskResult:
    histories = _load_histories(daily_log_read_url=daily_log_read_url, bearer_token=bearer_token, target_date=target_date, days=60)
    if len(histories) < 12:
        return FRiskResult(None, None, None, [], "insufficient_samples", {"history_count": len(histories)})

    labels = label_notes_in_batches(histories)
    df = build_daily_feature_table(histories, labels)
    if "expense_f_count" not in df.columns:
        return FRiskResult(None, None, None, [], "no_f_history", {"reason": "missing_expense_f_count"})

    work = df.copy().sort_values("date").reset_index(drop=True)
    work["f_event_flag"] = (work["expense_f_count"].fillna(0) > 0).astype(int)
    train = work.iloc[:-1].copy()
    if len(train) < 10:
        return FRiskResult(None, None, None, [], "insufficient_samples", {"train_rows": len(train)})
    if train["f_event_flag"].sum() == 0:
        return FRiskResult(None, None, None, [], "no_f_history", {"train_rows": len(train)})
    if train["f_event_flag"].nunique() < 2:
        return FRiskResult(None, None, None, [], "single_class_target", {"positive_count": int(train["f_event_flag"].sum())})

    pattern_summary = _explore_patterns(train)
    model_summary = _fit_model(train, work.iloc[[-1]])

    if model_summary.get("skipped_reason"):
        return FRiskResult(None, None, None, [], model_summary["skipped_reason"], {"pattern": pattern_summary, "model": model_summary})

    score = model_summary.get("score")
    matched = model_summary.get("matched_features", [])
    threshold = 0.72
    if score is None or score < threshold or not matched:
        return FRiskResult(None, score, "score_below_threshold_or_no_match", matched, None, {"pattern": pattern_summary, "model": model_summary, "threshold": threshold})

    reason = f"f_event_flag=Expense F Count>0 を目的変数に機械学習判定。score={score:.3f}"
    alert = "今日は過去にF支出が出た日と似た条件が重なっています。" + " / ".join(matched[:3]) + "。不要な購入判断は一呼吸置いてください。"
    return FRiskResult(alert, score, reason, matched, None, {"pattern": pattern_summary, "model": model_summary, "threshold": threshold})


def _load_histories(*, daily_log_read_url: str, bearer_token: Optional[str], target_date: str, days: int) -> list[DailyLogSummary]:
    base = datetime.strptime(target_date, "%Y-%m-%d")
    out: list[DailyLogSummary] = []
    for offset in range(days):
        day = (base - timedelta(days=offset)).strftime("%Y-%m-%d")
        summary = read_daily_log(daily_log_read_url=daily_log_read_url, target_date=day, bearer_token=bearer_token)
        if summary:
            out.append(summary)
    return out


def _explore_patterns(train: Any) -> dict[str, Any]:
    risk_days = train[train["f_event_flag"] == 1]
    safe_days = train[train["f_event_flag"] == 0]
    features = [
        "sleep_hours",
        "sleep_score",
        "spending_total",
        "weather_temp_max_c",
        "weather_precip_probability_max",
        "task_drop_count",
        "notes_stress_flag",
        "is_weekend",
    ]
    deltas: list[dict[str, Any]] = []
    for feature in features:
        if feature not in train.columns:
            continue
        risk_mean = float(risk_days[feature].fillna(0).mean()) if len(risk_days) else 0.0
        safe_mean = float(safe_days[feature].fillna(0).mean()) if len(safe_days) else 0.0
        deltas.append({"feature": feature, "delta": round(risk_mean - safe_mean, 3), "risk_mean": round(risk_mean, 3), "safe_mean": round(safe_mean, 3)})
    return {"deltas": sorted(deltas, key=lambda x: abs(x["delta"]), reverse=True)[:6]}


def _fit_model(train: Any, today_row: Any) -> dict[str, Any]:
    import importlib

    if importlib.util.find_spec("lightgbm") is not None:
        try:
            import lightgbm as lgb
            return _fit_lightgbm(train, today_row, lgb)
        except Exception:
            pass

    if importlib.util.find_spec("sklearn") is None:
        return {"skipped_reason": "ml_lib_not_installed"}

    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler

        x, y, today_x, cols_num, cols_cat = _build_xy(train, today_row)
        pre = ColumnTransformer(
            transformers=[
                ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]), cols_num),
                ("cat", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("oh", OneHotEncoder(handle_unknown="ignore"))]), cols_cat),
            ]
        )
        model = Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=600, class_weight="balanced"))])
        model.fit(x, y)
        score = float(model.predict_proba(today_x)[0][1])
        matched = _derive_matched_features(today_row.iloc[0].to_dict())
        return {"score": score, "matched_features": matched, "model": "logistic_regression", "skipped_reason": None}
    except Exception:
        return {"skipped_reason": "fit_exception"}


def _fit_lightgbm(train: Any, today_row: Any, lgb: Any) -> dict[str, Any]:
    x, y, today_x, _, _ = _build_xy(train, today_row)
    x_enc = _to_numeric_frame(x)
    today_enc = _to_numeric_frame(today_x, template_columns=x_enc.columns)
    clf = lgb.LGBMClassifier(n_estimators=160, learning_rate=0.05, num_leaves=15, random_state=42, class_weight="balanced", verbose=-1)
    clf.fit(x_enc, y)
    score = float(clf.predict_proba(today_enc)[0][1])
    matched = _derive_matched_features(today_row.iloc[0].to_dict())
    return {"score": score, "matched_features": matched, "model": "lightgbm", "skipped_reason": None}


def _build_xy(train: Any, today_row: Any):
    features = [
        "sleep_hours", "sleep_score", "spending_total", "task_drop_count", "task_done_count", "notes_stress_flag",
        "notes_fatigue_flag", "weather_temp_max_c", "weather_temp_min_c", "weather_precip_probability_max", "weather_code",
        "is_weekend", "place", "location_summary",
    ]
    for feature in features:
        if feature not in train.columns:
            train[feature] = None
        if feature not in today_row.columns:
            today_row[feature] = None

    x = train[features].copy()
    y = train["f_event_flag"].astype(int)
    today_x = today_row[features].copy()
    cols_num = [c for c in x.columns if c not in {"place", "location_summary"}]
    cols_cat = ["place", "location_summary"]
    return x, y, today_x, cols_num, cols_cat


def _to_numeric_frame(df: Any, template_columns: Any = None) -> Any:
    import pandas as pd

    work = df.copy()
    for col in work.columns:
        if str(work[col].dtype) in {"object", "string"}:
            work[col] = work[col].fillna("unknown")
    work = pd.get_dummies(work, dummy_na=True)
    if template_columns is not None:
        for col in template_columns:
            if col not in work.columns:
                work[col] = 0
        work = work[list(template_columns)]
    return work


def _derive_matched_features(today: dict[str, Any]) -> list[str]:
    matched: list[str] = []
    sleep_hours = _to_float(today.get("sleep_hours"))
    rain_prob = _to_float(today.get("weather_precip_probability_max"))
    drops = _to_float(today.get("task_drop_count"))
    stress = bool(today.get("notes_stress_flag"))

    if sleep_hours is not None and sleep_hours < 6:
        matched.append("睡眠時間が短め")
    if rain_prob is not None and rain_prob >= 50:
        matched.append("降水確率が高い")
    if drops is not None and drops >= 2:
        matched.append("Drop件数が多い傾向")
    if stress:
        matched.append("Notesストレスシグナル")
    return matched


def _to_float(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
