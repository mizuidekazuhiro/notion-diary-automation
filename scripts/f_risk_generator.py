from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Optional

from publish.read_daily_log import DailyLogSummary, read_daily_log
from scripts.note_batch_labeler import label_notes_in_batches
from scripts.openai_chat_utils import chat_completion
from scripts.expense_f_aggregator import aggregate_expense_f_for_dates
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
        "直近数日パターンと過去F日の類似、主要一致要因1〜3個、一致強度（強/中/弱）を必ず含める。"
        "explanation_points を最優先で使い、risk_json に無い理由を足さない。"
        "過剰に煽らない。出力は本文のみ。\n"
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


def generate_f_risk(*, daily_log_read_url: str, bearer_token: Optional[str], target_date: str) -> FRiskResult:
    risk_json: dict[str, Any] = {
        "target_date": target_date,
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
    histories = _load_histories(daily_log_read_url=daily_log_read_url, bearer_token=bearer_token, target_date=target_date, days=180)
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
        risk_json["skipped_reason"] = "labeling_failed"
        risk_json["no_alert_reason"] = "insufficient_evidence"
        risk_json["notes_labeling_ok"] = False
        logging.warning(
            "[FRisk][NotesAudit] skip labeling_failed fatal_reason=%s",
            note_audit.get("labeling_failed_fatal_reason"),
        )
        return FRiskResult(None, None, None, [], "labeling_failed", {"risk_json": risk_json, "note_label_audit": note_audit})

    df = build_daily_feature_table(histories, labels)
    work = df.copy().sort_values("date").reset_index(drop=True)
    work["f_event_flag"] = (work["expense_f_count"].fillna(0) > 0).astype(int)
    train = work.iloc[:-1].copy()
    today = work.iloc[[-1]].copy()
    if len(train) < 12:
        risk_json["skipped_reason"] = "insufficient_samples"
        risk_json["no_alert_reason"] = "insufficient_evidence"
        return FRiskResult(None, None, None, [], "insufficient_samples", {"risk_json": risk_json})
    if train["f_event_flag"].sum() == 0 or train["f_event_flag"].nunique() < 2:
        risk_json["skipped_reason"] = "no_f_history"
        risk_json["no_alert_reason"] = "insufficient_evidence"
        return FRiskResult(None, None, None, [], "no_f_history", {"risk_json": risk_json})

    pattern_summary = _explore_patterns(train)
    recent_train = train.tail(max(45, min(90, len(train))))
    availability = _build_input_availability(work)
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
    similarity = _f_day_similarity(train=train, today=today.iloc[0].to_dict())
    explanation_points = _build_explanation_points(matched=matched, similarity=similarity, today=today.iloc[0].to_dict())
    evidence_sufficiency = "sufficient" if len(explanation_points) >= 2 else "limited"
    confidence = "high" if len(train) >= 60 else "medium" if len(train) >= 30 else "low"
    reliability = "high" if similarity["strength"] == "strong" else "medium" if similarity["strength"] == "medium" else "low"

    model_risk_matched = bool(blended is not None and blended >= 0.62 and len(matched) >= 1 and similarity["strength"] in {"strong", "medium"} and len(explanation_points) >= 2)
    risk_matched = model_risk_matched
    if fallback_used:
        risk_matched = bool(fallback_meta.get("risk_matched"))
        blended = _to_float(fallback_meta.get("blended_score"))
        if risk_matched and fallback_meta.get("matched_factors"):
            matched = [str(x) for x in fallback_meta.get("matched_factors", [])]
            explanation_points = [f"fallback判定: {f}" for f in matched[:3]] + explanation_points[:2]

    risk_json.update(
        {
            "risk_matched": risk_matched,
            "risk_probability_recent": recent_score,
            "risk_probability_longterm": long_score,
            "recent_pattern_matches": similarity["pattern_matches"],
            "f_day_similarity_summary": similarity["summary"],
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
                fallback_meta.get("no_alert_reason")
                if fallback_used
                else ("not_matched" if evidence_sufficiency == "sufficient" else "insufficient_evidence")
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
            "notes_labeling_ok": True,
            "notes_labeling_quality": "low" if merge_quality_low else "high",
            "schedule_features_used": availability["schedule_used"],
            "weather_features_used": availability["weather_used"],
            "fallback_used": fallback_used,
            "fallback_details": fallback_meta,
            "forbidden_inputs_used": False,
        }
    )
    logging.info(
        "f_risk_stage_a_summary target_date=%s feature_count=%s feature_group_counts=%s available=%s unavailable=%s excluded=%s fallback_used=%s model_recent=%s model_long=%s history_count=%s class_balance=%.3f risk_recent=%s risk_long=%s risk_blended=%s matched=%s protective=%s no_alert_reason=%s forbidden_inputs_used=%s",
        target_date,
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
    for offset in range(days):
        day = (base - timedelta(days=offset)).strftime("%Y-%m-%d")
        summary = read_daily_log(daily_log_read_url=daily_log_read_url, target_date=day, bearer_token=bearer_token)
        if summary:
            out.append(summary)
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
        "spending_total_rolling_mean_7d", "spending_total_rolling_sum_14d", "weather_precip_probability_max_lag_1",
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
