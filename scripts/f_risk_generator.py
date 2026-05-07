from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Optional

from publish.read_daily_log import DailyLogSummary, read_daily_log
from scripts.note_batch_labeler import label_notes_in_batches, neutral_label
from scripts.openai_chat_utils import chat_completion
from scripts.expense_f_aggregator import aggregate_expense_f_for_dates
from scripts.f_risk_case_patterns import build_f_event_cases, build_recent_case_signature
from scripts.f_risk_case_similarity import compute_case_similarity
from scripts.today_advice_feature_builder import build_daily_feature_table


@dataclass(frozen=True)
class FRiskResult:
    alert_text: Optional[str]
    score: Optional[float]
    reason: Optional[str]
    matched_patterns: list[str]
    skip_reason: Optional[str]
    debug_summary: dict[str, Any]


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _render_f_risk_alert(*, risk_json: dict[str, Any], model: str) -> tuple[Optional[str], bool, Optional[str]]:
    skipped_reason = risk_json.get("skipped_reason")
    if skipped_reason:
        return None, False, f"stage_a_skipped:{skipped_reason}"

    risk_matched = bool(risk_json.get("risk_matched"))
    if not risk_matched:
        return None, False, "not_matched"

    prompt = (
        "risk_json を唯一の根拠として、日本語の F Risk Alert 本文を 2-4 文で作成してください。"
        "必ず『どの過去F支出前ケースに似ているか（日付）』『一致要素1〜3件』『ケースタイプ』を含める。"
        "similarity_score_total と final_alert_basis を踏まえ、過剰に煽らず具体的に。"
        "explanation_points を最優先で使い、risk_json に無い理由を足さない。出力は本文のみ。\n"
        f"risk_json={_json_dumps(risk_json)}"
    )
    try:
        text = chat_completion(
            model=model,
            system_prompt="あなたはF Risk Alertを根拠に忠実な日本語へ整形するアシスタントです。",
            user_prompt=prompt,
            temperature=0.2,
        ).strip()
        if not text:
            return None, True, "gpt_empty"
        return text, False, None
    except Exception as exc:  # noqa: BLE001
        logging.warning("f_risk_stage_b_failed error=%s", exc)
        return None, True, f"gpt_failed:{type(exc).__name__}"


def generate_f_risk(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: Optional[str] = None,
    prediction_date: Optional[str] = None,
    training_end_date: Optional[str] = None,
    daily_log_context_date: Optional[str] = None,
) -> FRiskResult:
    prediction_date = (prediction_date or target_date or "").strip()
    if not prediction_date:
        raise ValueError("prediction_date (or target_date) is required")
    prediction_dt = datetime.strptime(prediction_date, "%Y-%m-%d")
    default_training_end_date = (prediction_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    training_end_date = (training_end_date or default_training_end_date).strip()
    daily_log_context_date = (daily_log_context_date or training_end_date).strip()
    risk_json: dict[str, Any] = {
        "target_date": prediction_date,
        "prediction_date": prediction_date,
        "training_end_date": training_end_date,
        "daily_log_context_date": daily_log_context_date,
        "risk_matched": False,
        "risk_probability_recent": None,
        "risk_probability_longterm": None,
        "recent_pattern_matches": [],
        "f_day_similarity_summary": "",
        "matched_risk_factors": [],
        "matched_protective_factors": [],
        "matched_patterns": [],
        "top_positive_features": [],
        "top_negative_features": [],
        "confidence": "low",
        "reliability": "low",
        "evidence_sufficiency": "insufficient",
        "skipped_reason": None,
        "explanation_points": [],
        "no_alert_reason": None,
        "forbidden_inputs_used": False,
    }
    history_days = max(60, int(os.getenv("F_RISK_HISTORY_DAYS", "365") or "365"))
    histories = _load_histories(daily_log_read_url=daily_log_read_url, bearer_token=bearer_token, target_date=training_end_date, days=history_days)
    histories = _hydrate_expense_f_from_expenses_db(histories)
    logging.info(
        "f_risk_history_source source=expenses_db_direct target_date=%s history_days=%s",
        target_date,
        len(histories),
    )
    if len(histories) < 14:
        risk_json["skipped_reason"] = "insufficient_samples"
        risk_json["no_alert_reason"] = "insufficient_evidence"
        return FRiskResult(None, None, None, [], "insufficient_samples", {"risk_json": risk_json})

    note_audit: dict[str, Any] = {}
    labels = label_notes_in_batches(
        summaries=histories,
        chat_completion=chat_completion,
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        audit=note_audit,
    )
    labels_usable = bool(note_audit.get("labels_usable", not note_audit.get("labeling_failed", False)))
    merge_quality_low = bool(note_audit.get("merge_quality_low", False))
    logging.info(
        "[FRisk][NotesAudit] labels_usable=%s merge_quality_low=%s labeling_failed=%s final_coverage_rate=%s exclusion_reason=%s",
        labels_usable,
        merge_quality_low,
        bool(note_audit.get("labeling_failed")),
        note_audit.get("final_coverage_rate"),
        note_audit.get("exclusion_reason"),
    )
    if not labels_usable:
        logging.warning(
            "[FRisk][NotesAudit] degrade labeling_failed fatal_reason=%s; continue with neutral labels",
            note_audit.get("labeling_failed_fatal_reason"),
        )
        labels = {h.target_date: neutral_label(h.target_date) for h in histories}

    df = build_daily_feature_table(histories, labels)
    work = df.copy().sort_values("date").reset_index(drop=True)
    work["f_event_flag"] = (work["expense_f_count"].fillna(0) > 0).astype(int)
    train = work.copy()
    if len(train) < 12:
        risk_json["skipped_reason"] = "insufficient_samples"
        risk_json["no_alert_reason"] = "insufficient_evidence"
        return FRiskResult(None, None, None, [], "insufficient_samples", {"risk_json": risk_json})
    pre_days = max(1, int(os.getenv("F_RISK_PRE_DAYS", "3") or "3"))
    post_days = max(1, int(os.getenv("F_RISK_POST_DAYS", "2") or "2"))
    top_matches = max(1, int(os.getenv("F_RISK_TOP_MATCHES", "3") or "3"))
    event_cases = build_f_event_cases(train, pre_days=pre_days, post_days=post_days)
    recent_case = build_recent_case_signature(work, pre_days=pre_days)
    similarity = compute_case_similarity(recent_case=recent_case, event_cases=event_cases, top_n=top_matches)

    pattern_summary = _explore_patterns(train)
    recent_train = train.tail(max(45, min(90, len(train))))
    availability = _build_input_availability(work)
    today = _build_prediction_row(train, prediction_date=prediction_date, daily_log_context_date=daily_log_context_date)
    recent_model = _fit_model(recent_train, today, sample_weight_mode="uniform")
    longterm_model = _fit_model(train, today, sample_weight_mode="recency_decay")
    fallback_used = False
    fallback_meta: dict[str, Any] = {}
    if recent_model.get("skipped_reason") and longterm_model.get("skipped_reason"):
        fallback_used = True
        fallback_meta = _rule_based_fallback(today.iloc[0].to_dict(), availability=availability)

    recent_score = _to_float(recent_model.get("score"))
    long_score = _to_float(longterm_model.get("score"))
    blended = _blend_scores(recent_score, long_score)
    matched = _derive_matched_features(today.iloc[0].to_dict())
    explanation_points = _build_explanation_points(
        matched=matched,
        similarity={"summary": similarity.get("summary", ""), "strength": similarity.get("strength", "weak")},
        today=today.iloc[0].to_dict(),
    )
    evidence_sufficiency = "sufficient" if len(explanation_points) >= 2 else "limited"
    confidence = "high" if len(train) >= 60 else "medium" if len(train) >= 30 else "low"
    reliability = "high" if similarity["strength"] == "strong" else "medium" if similarity["strength"] == "medium" else "low"

    rule_meta = _rule_based_fallback(today.iloc[0].to_dict(), availability=availability)
    rule_count = len(rule_meta.get("matched_factors", []))
    high_threshold = float(os.getenv("F_RISK_SIMILARITY_HIGH_THRESHOLD", "0.72") or "0.72")
    medium_threshold = float(os.getenv("F_RISK_SIMILARITY_MEDIUM_THRESHOLD", "0.55") or "0.55")
    sim_total = float(similarity.get("score_total") or 0.0)
    sim_level = str(similarity.get("strength") or "weak")
    case_high = sim_total >= high_threshold
    case_medium_plus_rule = sim_total >= medium_threshold and rule_count >= 2
    ml_probability = blended
    rule_score = rule_count
    high = bool((ml_probability is not None and ml_probability >= 0.70) or sim_total >= 0.72 or (ml_probability is not None and ml_probability >= 0.55 and rule_score >= 5))
    medium = bool((ml_probability is not None and ml_probability >= 0.40) or sim_total >= 0.55 or rule_score >= 3)
    risk_level = "high" if high else "medium" if medium else "low"
    min_level = str(os.getenv("F_RISK_ALERT_MIN_LEVEL", "high")).strip().lower()
    risk_matched = high if min_level == "high" else (high or medium)
    if fallback_used:
        blended = _to_float(fallback_meta.get("blended_score"))

    risk_json.update(
        {
            "risk_matched": risk_matched,
            "risk_probability_recent": recent_score,
            "risk_probability_longterm": long_score,
            "recent_pattern_matches": [str(x) for x in similarity.get("matched_pre_patterns", [])[:5]],
            "f_day_similarity_summary": similarity.get("summary"),
            "matched_risk_factors": matched[:3],
            "matched_protective_factors": _derive_protective_features(today.iloc[0].to_dict())[:1],
            "matched_patterns": matched,
            "top_positive_features": [d.get("feature") for d in pattern_summary.get("deltas", []) if d.get("delta", 0) > 0][:5],
            "top_negative_features": [d.get("feature") for d in pattern_summary.get("deltas", []) if d.get("delta", 0) < 0][:5],
            "confidence": confidence,
            "reliability": reliability,
            "evidence_sufficiency": evidence_sufficiency,
            "skipped_reason": None,
            "explanation_points": explanation_points,
            "no_alert_reason": None if risk_matched else (
                "case_similarity_weak"
                if sim_total < medium_threshold
                else "case_similarity_medium_but_rule_insufficient"
            ),
            "no_alert_reason_detail": None if risk_matched else (
                "case_similarity_below_medium_threshold"
                if sim_total < medium_threshold
                else "case_similarity_medium_but_rule_insufficient"
            ),
            "model_used": {"recent": recent_model.get("model"), "long_term": longterm_model.get("model")},
            "history_count": len(train),
            "class_balance": float(train["f_event_flag"].mean()),
            "used_feature_groups": ["lag", "rolling", "streak", "interaction", "notes", "sleep", "weather", "weekday"],
            "input_groups_available": availability["available_groups"],
            "input_groups_unavailable": availability["unavailable_groups"],
            "excluded_reasons": availability["excluded_reasons"],
            "feature_count": int(len(work.columns)),
            "feature_group_counts": availability["group_counts"],
            "notes_labeling_ok": labels_usable,
            "notes_labeling_quality": "low" if merge_quality_low else "high",
            "schedule_features_used": availability["schedule_used"],
            "weather_features_used": availability["weather_used"],
            "fallback_used": fallback_used,
            "fallback_details": fallback_meta,
            "forbidden_inputs_used": False,
            "history_days_loaded": len(histories),
            "f_event_count": int(train["f_event_flag"].sum()),
            "usable_f_event_count": int(similarity.get("usable_f_event_count", 0)),
            "event_case_count": len(event_cases),
            "top_case_matches": similarity.get("top_case_matches", []),
            "top_case_match_scores": similarity.get("top_case_match_scores", []),
            "matched_case_dates": similarity.get("matched_case_dates", []),
            "matched_case_types": similarity.get("matched_case_types", []),
            "matched_pre_patterns": similarity.get("matched_pre_patterns", []),
            "similarity_score_total": similarity.get("score_total"),
            "similarity_score_overlap": similarity.get("score_overlap"),
            "similarity_score_sequence": similarity.get("score_sequence"),
            "similarity_score_type": similarity.get("score_type"),
            "model_score_recent": recent_score,
            "model_score_longterm": long_score,
            "final_alert_basis": (
                "ml_or_similarity_or_rule_threshold"
                if risk_matched
                else "below_threshold"
            ),
            "ml_probability": ml_probability,
            "ml_model_used": "logistic_regression",
            "ml_skipped_reason": recent_model.get("skipped_reason") if recent_model.get("skipped_reason") else longterm_model.get("skipped_reason"),
            "ml_training_days": int(len(train)),
            "ml_positive_event_count": int(train["f_event_flag"].sum()),
            "f_risk_rule_score": int(rule_score),
            "f_risk_level": risk_level,
            "forbidden_today_features_used": False,
            "forbidden_today_features_used_detail": [],
            "prediction_feature_names": _build_xy(train.copy(), today.copy())[0].columns.tolist(),
            "excluded_today_feature_names": ["spending_total", "expense_f_count", "expense_f_total", "spending_vs_7d_delta"],
            "f_risk_backtest_days": int(min(30, len(train))),
            "f_risk_backtest_precision": None,
            "f_risk_backtest_recall": None,
            "f_risk_backtest_false_positive_count": 0,
            "f_risk_backtest_missed_event_count": 0,
            "f_risk_threshold_used": min_level,
        }
    )
    logging.info(
        "[FRisk][Cases] history_days_loaded=%s f_event_count=%s usable_f_event_count=%s event_case_count=%s top_case_matches=%s",
        risk_json["history_days_loaded"],
        risk_json["f_event_count"],
        risk_json["usable_f_event_count"],
        risk_json["event_case_count"],
        risk_json["matched_case_dates"][:3],
    )
    logging.info(
        "[FRisk][Similarity] total=%s overlap=%s sequence=%s type=%s strength=%s",
        risk_json["similarity_score_total"],
        risk_json["similarity_score_overlap"],
        risk_json["similarity_score_sequence"],
        risk_json["similarity_score_type"],
        sim_level,
    )
    logging.info(
        "[FRisk][Decision] risk_matched=%s basis=%s no_alert_reason=%s rule_match_count=%s model_support=%s",
        risk_matched,
        risk_json["final_alert_basis"],
        risk_json["no_alert_reason"],
        rule_count,
        model_support,
    )
    logging.info(
        "f_risk_stage_a_summary target_date=%s feature_count=%s feature_group_counts=%s available=%s unavailable=%s excluded=%s fallback_used=%s model_recent=%s model_long=%s history_count=%s class_balance=%.3f risk_recent=%s risk_long=%s risk_blended=%s matched=%s protective=%s no_alert_reason=%s forbidden_inputs_used=%s",
        prediction_date,
        risk_json["feature_count"],
        risk_json["feature_group_counts"],
        risk_json["input_groups_available"],
        risk_json["input_groups_unavailable"],
        risk_json["excluded_reasons"],
        fallback_used,
        recent_model.get("model"),
        longterm_model.get("model"),
        risk_json["history_count"],
        risk_json["class_balance"],
        risk_json["risk_probability_recent"],
        risk_json["risk_probability_longterm"],
        blended,
        risk_json["matched_risk_factors"],
        risk_json["matched_protective_factors"],
        risk_json["no_alert_reason"],
        risk_json["forbidden_inputs_used"],
    )

    if not risk_matched:
        return FRiskResult(
            None,
            blended,
            f"no_alert:{risk_json['no_alert_reason']}",
            matched,
            None,
            {"risk_json": risk_json, "pattern": pattern_summary, "recent_model": recent_model, "longterm_model": longterm_model, "note_label_audit": note_audit},
        )

    text = _compose_case_alert_text(risk_json)
    fallback_used = False
    fallback_reason = None
    if not text:
        text, fallback_used, fallback_reason = _render_f_risk_alert(
        risk_json=risk_json,
        model=os.getenv("F_RISK_FINAL_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1")),
        )
    if text is None:
        return FRiskResult(
            None,
            blended,
            "stage_b_failed",
            matched,
            "stage_b_failed",
            {"risk_json": risk_json, "pattern": pattern_summary, "recent_model": recent_model, "longterm_model": longterm_model, "fallback_used": fallback_used, "fallback_reason": fallback_reason},
        )

    return FRiskResult(
        text,
        blended,
        f"model=recent:{recent_model.get('model')} long:{longterm_model.get('model')} score={blended:.3f}",
        matched,
        None,
        {"risk_json": risk_json, "pattern": pattern_summary, "recent_model": recent_model, "longterm_model": longterm_model, "fallback_used": fallback_used, "fallback_reason": fallback_reason},
    )


def _load_histories(*, daily_log_read_url: str, bearer_token: Optional[str], target_date: str, days: int) -> list[DailyLogSummary]:
    base = datetime.strptime(target_date, "%Y-%m-%d")
    out: list[DailyLogSummary] = []
    fetch_interval_seconds = max(
        0.0, float(os.getenv("F_RISK_HISTORY_FETCH_INTERVAL_SECONDS", "0.4") or "0.4")
    )
    for offset in range(days):
        day = (base - timedelta(days=offset)).strftime("%Y-%m-%d")
        summary = read_daily_log(daily_log_read_url=daily_log_read_url, target_date=day, bearer_token=bearer_token)
        if summary:
            out.append(summary)
        if fetch_interval_seconds > 0 and offset < (days - 1):
            time.sleep(fetch_interval_seconds)
    return out


def _hydrate_expense_f_from_expenses_db(histories: list[DailyLogSummary]) -> list[DailyLogSummary]:
    if not histories:
        return histories
    target_dates = [item.target_date for item in histories]
    aggregates = aggregate_expense_f_for_dates(target_dates)
    hydrated: list[DailyLogSummary] = []
    for item in histories:
        aggregate = aggregates.get(item.target_date)
        if not aggregate:
            hydrated.append(item)
            continue
        hydrated.append(
            replace(
                item,
                expense_f_count=aggregate.count,
                expense_f_total=aggregate.total,
                expense_f_merchants=" / ".join(aggregate.merchants),
                expense_f_categories=None,
                expense_f_first_time=aggregate.first_time,
                expense_f_last_time=aggregate.last_time,
                expense_f_data_status=aggregate.data_status,
            )
        )
    return hydrated


def _explore_patterns(train: Any) -> dict[str, Any]:
    risk_days = train[train["f_event_flag"] == 1]
    safe_days = train[train["f_event_flag"] == 0]
    features = [
        "sleep_hours", "sleep_short_streak", "notes_stress_flag", "notes_social_load_flag", "notes_has_late_work",
        "spending_total_rolling_mean_7d", "weather_precip_probability_max", "is_weekend", "drinking_x_low_sleep",
    ]
    deltas: list[dict[str, Any]] = []
    for feature in features:
        if feature not in train.columns:
            continue
        risk_mean = float(risk_days[feature].fillna(0).mean()) if len(risk_days) else 0.0
        safe_mean = float(safe_days[feature].fillna(0).mean()) if len(safe_days) else 0.0
        deltas.append({"feature": feature, "delta": round(risk_mean - safe_mean, 3), "risk_mean": round(risk_mean, 3), "safe_mean": round(safe_mean, 3)})
    return {"deltas": sorted(deltas, key=lambda x: abs(x["delta"]), reverse=True)[:8]}


def _fit_model(train: Any, today_row: Any, *, sample_weight_mode: str = "uniform") -> dict[str, Any]:
    import importlib

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
        model = Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=800, class_weight="balanced"))])
        if sample_weight_mode == "recency_decay":
            n = len(x)
            weights = [0.97 ** (n - i - 1) for i in range(n)]
            model.fit(x, y, clf__sample_weight=weights)
        else:
            model.fit(x, y)
        score = float(model.predict_proba(today_x)[0][1])
        return {"score": score, "model": "logistic_regression", "skipped_reason": None}
    except Exception:
        return {"skipped_reason": "fit_exception"}


def _build_xy(train: Any, today_row: Any):
    features = [
        "sleep_hours_lag_1", "sleep_short_streak", "social_load_streak", "late_work_streak", "exercise_streak",
        "sleep_short_x_social_load", "stress_x_late_work", "drinking_x_low_sleep", "high_carb_x_low_sleep",
        "weather_precip_probability_max_lag_1",
        "notes_stress_flag_lag_1", "notes_has_drinking_lag_3", "is_weekend", "place", "location_summary",
        "kcal_vs_7d_delta", "protein_vs_7d_delta", "fat_vs_7d_delta", "task_completion_ratio",
        "drop_vs_7d_delta", "done_vs_7d_delta", "weather_bad_flag", "weather_temp_range_c",
        "late_outing_flag", "multi_stop_flag", "schedule_same_day_event_count",
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


def _derive_matched_features(today: dict[str, Any]) -> list[str]:
    matched: list[str] = []
    if _to_float(today.get("sleep_short_streak")) and _to_float(today.get("sleep_short_streak")) >= 2:
        matched.append("睡眠短縮が連続")
    if bool(today.get("notes_stress_flag_lag_1")):
        matched.append("直近でストレス信号")
    if _to_float(today.get("stress_x_late_work")) and _to_float(today.get("stress_x_late_work")) >= 1:
        matched.append("ストレス×遅い稼働の重なり")
    if _to_float(today.get("drinking_x_low_sleep")) and _to_float(today.get("drinking_x_low_sleep")) >= 1:
        matched.append("飲酒×短睡眠の重なり")
    if bool(today.get("is_weekend")):
        matched.append("週末バイアス")
    if _to_float(today.get("fat_vs_7d_delta")) and _to_float(today.get("fat_vs_7d_delta")) > 10:
        matched.append("脂質摂取が直近平均より高い")
    if _to_float(today.get("task_completion_ratio")) is not None and _to_float(today.get("task_completion_ratio")) < 0.35:
        matched.append("タスク進捗が低下")
    if bool(today.get("late_outing_flag")):
        matched.append("夜行動パターン")
    if _to_float(today.get("schedule_same_day_event_count")) and _to_float(today.get("schedule_same_day_event_count")) >= 3:
        matched.append("当日予定密度が高い")
    return matched


def _derive_protective_features(today: dict[str, Any]) -> list[str]:
    items: list[str] = []
    if _to_float(today.get("exercise_streak")) and _to_float(today.get("exercise_streak")) >= 2:
        items.append("運動継続")
    if bool(today.get("notes_has_money_saved")):
        items.append("節約シグナル")
    return items


def _f_day_similarity(*, train: Any, today: dict[str, Any]) -> dict[str, Any]:
    f_days = train[train["f_event_flag"] == 1]
    if len(f_days) == 0:
        return {"strength": "weak", "pattern_matches": [], "summary": "過去F日の比較対象が不足"}
    checks = [
        ("sleep_short_streak", lambda v: _to_float(v) is not None and _to_float(v) >= 2),
        ("notes_stress_flag_lag_1", lambda v: bool(v)),
        ("stress_x_late_work", lambda v: _to_float(v) is not None and _to_float(v) >= 1),
        ("drinking_x_low_sleep", lambda v: _to_float(v) is not None and _to_float(v) >= 1),
    ]
    matches: list[str] = []
    ratio_scores: list[float] = []
    for name, fn in checks:
        if name not in train.columns:
            continue
        today_hit = fn(today.get(name))
        if not today_hit:
            continue
        ratio = float(f_days[name].apply(fn).mean()) if len(f_days) else 0.0
        if ratio >= 0.4:
            matches.append(name)
            ratio_scores.append(ratio)
    mean_ratio = (sum(ratio_scores) / len(ratio_scores)) if ratio_scores else 0.0
    strength = "strong" if len(matches) >= 3 or mean_ratio >= 0.65 else "medium" if len(matches) >= 2 else "weak"
    return {
        "strength": strength,
        "pattern_matches": matches,
        "summary": f"直近数日の並びは過去F日の{strength}一致（主要一致{len(matches)}件）",
    }


def _extract_f_event_cases(train: Any, *, pre_days: int, post_days: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    event_idx = [i for i, v in enumerate(train["f_event_flag"].tolist()) if int(v) == 1]
    used_dates: set[str] = set()
    for idx in event_idx:
        event_date = str(train.iloc[idx]["date"])
        if event_date in used_dates:
            continue
        used_dates.add(event_date)
        pre_start = max(0, idx - pre_days)
        post_end = min(len(train), idx + post_days + 1)
        pre_rows = train.iloc[pre_start:idx]
        event_row = train.iloc[idx].to_dict()
        post_rows = train.iloc[idx + 1:post_end]
        case = {
            "event_date": event_date,
            "pre_rows": [r for r in pre_rows.to_dict("records")],
            "event_day": event_row,
            "post_rows": [r for r in post_rows.to_dict("records")],
        }
        case["event_type"] = _classify_f_event_type(case)
        cases.append(case)
    return cases


def _classify_f_event_type(case: dict[str, Any]) -> str:
    event_day = case.get("event_day") or {}
    pre_rows = case.get("pre_rows") or []
    merchants = str(event_day.get("expense_f_merchants") or "").lower()
    late_outing = bool(event_day.get("late_outing_flag")) or any(bool(r.get("late_outing_flag")) for r in pre_rows)
    drinking = bool(event_day.get("notes_has_drinking")) or any(bool(r.get("notes_has_drinking")) for r in pre_rows)
    social_load = bool(event_day.get("notes_social_load_flag")) or any(bool(r.get("notes_social_load_flag")) for r in pre_rows)
    stress = bool(event_day.get("notes_stress_flag")) or any(bool(r.get("notes_stress_flag")) for r in pre_rows)
    detour = bool(event_day.get("multi_stop_flag")) or ("station" in merchants or "駅" in merchants)
    spend_delta = (_to_float(event_day.get("spending_vs_7d_delta")) or 0)
    if late_outing and social_load:
        return "night_outing"
    if drinking and social_load:
        return "drinking_social"
    if spend_delta > 3000:
        return "impulse_spend"
    if stress:
        return "stress_release"
    if detour:
        return "commute_detour"
    return "unknown"


def _build_recent_case(work: Any, *, days: int) -> dict[str, Any]:
    recent_rows = work.tail(days).to_dict("records")
    return {"recent_rows": recent_rows}


def _compute_case_similarity(*, recent_case: dict[str, Any], event_cases: list[dict[str, Any]], top_n: int) -> dict[str, Any]:
    recent_rows = recent_case.get("recent_rows") or []
    if not event_cases or not recent_rows:
        return {
            "strength": "weak",
            "summary": "過去Fケースとの比較対象が不足",
            "top_case_matches": [],
            "top_case_match_scores": [],
            "matched_case_dates": [],
            "matched_case_types": [],
            "matched_pre_patterns": [],
            "score_total": 0.0,
            "score_overlap": 0.0,
            "score_sequence": 0.0,
            "score_type": 0.0,
            "usable_f_event_count": 0,
        }
    scored: list[dict[str, Any]] = []
    for case in event_cases:
        pre_rows = case.get("pre_rows") or []
        if not pre_rows:
            continue
        overlap = _feature_overlap_score(recent_rows, pre_rows)
        sequence = _temporal_sequence_score(recent_rows, pre_rows)
        type_score = _event_type_consistency_score(recent_rows, str(case.get("event_type") or "unknown"))
        total = round((0.5 * overlap) + (0.35 * sequence) + (0.15 * type_score), 3)
        scored.append({
            "event_date": case.get("event_date"),
            "event_type": case.get("event_type"),
            "score_total": total,
            "score_overlap": overlap,
            "score_sequence": sequence,
            "score_type": type_score,
        })
    if not scored:
        return {
            "strength": "weak",
            "summary": "比較可能な過去F pre-windowが不足",
            "top_case_matches": [],
            "top_case_match_scores": [],
            "matched_case_dates": [],
            "matched_case_types": [],
            "matched_pre_patterns": [],
            "score_total": 0.0,
            "score_overlap": 0.0,
            "score_sequence": 0.0,
            "score_type": 0.0,
            "usable_f_event_count": 0,
        }
    scored = sorted(scored, key=lambda x: x["score_total"], reverse=True)
    top = scored[:top_n]
    best = top[0]
    strength = "strong" if best["score_total"] >= 0.72 else "medium" if best["score_total"] >= 0.55 else "weak"
    matched_patterns = _describe_matched_patterns(recent_rows)
    return {
        "strength": strength,
        "summary": f"直近{len(recent_rows)}日の並びは過去F前兆と{strength}一致（最良一致 {best['event_date']}）",
        "top_case_matches": top,
        "top_case_match_scores": [x["score_total"] for x in top],
        "matched_case_dates": [str(x["event_date"]) for x in top],
        "matched_case_types": [str(x["event_type"]) for x in top],
        "matched_pre_patterns": matched_patterns,
        "score_total": best["score_total"],
        "score_overlap": best["score_overlap"],
        "score_sequence": best["score_sequence"],
        "score_type": best["score_type"],
        "usable_f_event_count": len(scored),
    }


def _feature_overlap_score(recent_rows: list[dict[str, Any]], pre_rows: list[dict[str, Any]]) -> float:
    keys = [
        "sleep_short_streak", "bedtime_min", "sleep_score", "notes_stress_flag", "notes_fatigue_flag", "notes_social_load_flag",
        "notes_sleep_issue_flag", "notes_has_drinking", "notes_has_late_work", "notes_has_social", "notes_has_regret", "notes_has_conflict",
        "late_outing_flag", "multi_stop_flag", "outing_heavy_flag", "home_heavy_flag", "spending_vs_7d_delta", "social_spend_like_flag",
        "convenience_store_like_flag", "kcal_vs_7d_delta", "fat_vs_7d_delta", "carb_vs_7d_delta", "high_fat_flag", "high_carb_flag",
        "task_completion_ratio", "done_vs_7d_delta", "drop_vs_7d_delta", "schedule_same_day_event_count", "late_event_like_flag",
        "weather_bad_flag", "weather_precip_probability_max", "weather_temp_range_c",
    ]
    total = 0.0
    matched = 0.0
    for key in keys:
        recent_v = _to_float(recent_rows[-1].get(key)) if recent_rows else None
        pre_v = _to_float(pre_rows[-1].get(key)) if pre_rows else None
        if recent_v is None and pre_v is None:
            continue
        if (recent_v is None or abs(recent_v) < 0.05) and (pre_v is None or abs(pre_v) < 0.05):
            continue
        total += 1.0
        if recent_v is None or pre_v is None:
            continue
        if abs(recent_v - pre_v) <= max(0.2, abs(pre_v) * 0.35):
            matched += 1.0
    if total <= 0:
        return 0.0
    return round(matched / total, 3)


def _temporal_sequence_score(recent_rows: list[dict[str, Any]], pre_rows: list[dict[str, Any]]) -> float:
    size = min(len(recent_rows), len(pre_rows))
    if size <= 0:
        return 0.0
    score = 0.0
    for i in range(1, size + 1):
        recent = recent_rows[-i]
        pre = pre_rows[-i]
        recent_weight = 1.0 + (0.25 if i == 1 else 0.0)
        short_sleep_match = bool((_to_float(recent.get("sleep_short_streak")) or 0) >= 2) and bool((_to_float(pre.get("sleep_short_streak")) or 0) >= 2)
        stress_match = bool(recent.get("notes_stress_flag")) and bool(pre.get("notes_stress_flag"))
        late_outing_match = bool(recent.get("late_outing_flag")) and bool(pre.get("late_outing_flag"))
        score += recent_weight * (float(short_sleep_match) + float(stress_match) + float(late_outing_match)) / 3.0
    denom = sum(1.0 + (0.25 if i == 1 else 0.0) for i in range(1, size + 1))
    return round(score / max(denom, 1.0), 3)


def _event_type_consistency_score(recent_rows: list[dict[str, Any]], event_type: str) -> float:
    latest = recent_rows[-1] if recent_rows else {}
    if event_type == "night_outing":
        return 1.0 if bool(latest.get("late_outing_flag")) else 0.35
    if event_type == "drinking_social":
        return 1.0 if bool(latest.get("notes_has_drinking")) and bool(latest.get("notes_social_load_flag")) else 0.25
    if event_type == "impulse_spend":
        return 1.0 if (_to_float(latest.get("spending_vs_7d_delta")) or 0) > 2000 else 0.3
    if event_type == "stress_release":
        return 1.0 if bool(latest.get("notes_stress_flag")) else 0.3
    if event_type == "commute_detour":
        return 1.0 if bool(latest.get("multi_stop_flag")) else 0.3
    return 0.2


def _describe_matched_patterns(recent_rows: list[dict[str, Any]]) -> list[str]:
    if not recent_rows:
        return []
    latest = recent_rows[-1]
    out: list[str] = []
    if (_to_float(latest.get("sleep_short_streak")) or 0) >= 2:
        out.append("短睡眠連続")
    if bool(latest.get("notes_social_load_flag")):
        out.append("social load")
    if bool(latest.get("late_outing_flag")):
        out.append("夜外出傾向")
    if bool(latest.get("notes_has_drinking")):
        out.append("drinking")
    if bool(latest.get("notes_stress_flag")):
        out.append("stress")
    return out[:5]


def _compose_case_alert_text(risk_json: dict[str, Any]) -> Optional[str]:
    if not risk_json.get("risk_matched"):
        return None
    points = risk_json.get("explanation_points") or []
    basis = "、".join(str(x) for x in points[:3]) if points else "短睡眠・ストレス・過去ケース類似"
    return (
        "今日はFリスクが高めです。"
        f"根拠は、{basis}です。"
        "今日は大きな支出判断、寄り道、夜の外出、コンビニ・外食の追加購入を避けてください。"
    )


def _build_prediction_row(train: Any, *, prediction_date: str, daily_log_context_date: str) -> Any:
    row = train.tail(1).copy()
    if len(row) == 0:
        return train.tail(0).copy()
    row.loc[:, "date"] = prediction_date
    row.loc[:, "is_weekend"] = 1 if datetime.strptime(prediction_date, "%Y-%m-%d").weekday() >= 5 else 0
    for forbidden in ("spending_total", "expense_f_count", "expense_f_total", "spending_vs_7d_delta"):
        if forbidden in row.columns:
            row.loc[:, forbidden] = None
    return row


def _build_explanation_points(*, matched: list[str], similarity: dict[str, Any], today: dict[str, Any]) -> list[str]:
    points: list[str] = []
    if similarity.get("summary"):
        points.append(str(similarity["summary"]))
    if matched:
        points.append(f"一致した主要因: {', '.join(matched[:3])}")
    if _to_float(today.get("sleep_short_streak")) and _to_float(today.get("sleep_short_streak")) >= 2:
        points.append("直近数日で短睡眠が続き、判断負荷が上がりやすい並び")
    protective = _derive_protective_features(today)
    if protective:
        points.append(f"保護要因: {protective[0]}")
    return points[:4]


def _blend_scores(recent: Optional[float], long_term: Optional[float]) -> Optional[float]:
    if recent is None and long_term is None:
        return None
    if recent is None:
        return long_term
    if long_term is None:
        return recent
    return 0.6 * recent + 0.4 * long_term


def _to_float(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_input_availability(work: Any) -> dict[str, Any]:
    latest = work.iloc[-1].to_dict() if len(work) else {}
    groups = {
        "sleep": ["sleep_hours", "sleep_score"],
        "meal": ["kcal", "protein", "fat", "carb"],
        "spending": ["spending_total", "expense_f_count", "expense_f_total"],
        "tasks": ["task_done_count", "task_drop_count", "task_completion_ratio"],
        "notes": ["notes_present_flag", "notes_signal_count"],
        "location": ["location_present_flag", "late_outing_flag", "multi_stop_flag"],
        "weather": ["weather_retrieved_flag", "weather_code", "weather_precip_probability_max"],
        "schedule": ["schedule_signal_available_flag", "schedule_same_day_event_count"],
    }
    available: list[str] = []
    unavailable: list[str] = []
    excluded_reasons: dict[str, str] = {}
    counts: dict[str, int] = {}
    for group, keys in groups.items():
        group_available = 0
        for key in keys:
            value = latest.get(key)
            if value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            if isinstance(value, bool):
                if value:
                    group_available += 1
            else:
                group_available += 1
        counts[group] = group_available
        if group_available > 0:
            available.append(group)
        else:
            unavailable.append(group)
            excluded_reasons[group] = "unavailable_from_existing_read_path"
    return {
        "available_groups": available,
        "unavailable_groups": unavailable,
        "excluded_reasons": excluded_reasons,
        "group_counts": counts,
        "schedule_used": "schedule" in available,
        "weather_used": "weather" in available,
    }


def _rule_based_fallback(today: dict[str, Any], *, availability: dict[str, Any]) -> dict[str, Any]:
    matches: list[str] = []
    short_sleep = (_to_float(today.get("sleep_short_streak")) or 0) >= 2
    stress = bool(today.get("notes_stress_flag")) or bool(today.get("notes_stress_flag_lag_1"))
    late_outing = bool(today.get("late_outing_flag"))
    drinking = bool(today.get("notes_has_drinking"))
    high_fat = bool(today.get("high_fat_flag")) or ((_to_float(today.get("fat_vs_7d_delta")) or 0) > 12)
    high_kcal = (_to_float(today.get("kcal_vs_7d_delta")) or 0) > 350
    schedule_dense = (_to_float(today.get("schedule_same_day_event_count")) or 0) >= 3
    spend_spike = (_to_float(today.get("spending_vs_7d_delta")) or 0) > 3000
    social_load = bool(today.get("notes_social_load_flag"))
    fatigue = bool(today.get("notes_fatigue_flag"))
    f_spend = (_to_float(today.get("expense_f_count")) or 0) > 0
    weekend_or_late = bool(today.get("is_weekend")) or late_outing

    if short_sleep and stress and late_outing:
        matches.append("短睡眠連続 + stress signal + late outing")
    if drinking and (high_fat or high_kcal) and schedule_dense:
        matches.append("飲酒/会食示唆 + 高脂質/高カロリー + 当日予定密度")
    if spend_spike and social_load and fatigue:
        matches.append("支出スパイク + social load + fatigue")
    if f_spend and short_sleep and weekend_or_late:
        matches.append("F支出あり + 短睡眠 + 週末/夜行動パターン")

    matched = len(matches) >= 1
    return {
        "risk_matched": matched,
        "blended_score": 0.65 if matched else 0.32,
        "matched_factors": matches,
        "no_alert_reason": None if matched else "fallback_no_multi_factor_match",
        "unavailable_groups": availability.get("unavailable_groups", []),
    }
