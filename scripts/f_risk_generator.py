from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Optional

from publish.read_daily_log import DailyLogSummary, DoneTaskDetail, ExpenseSummary, read_daily_log
from ingest.http_client import fetch_json
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
        "risk_json ã‚’å”¯ä¸€ã®æ ¹æ‹ ã¨ã—ã¦ã€æ—¥æœ¬èªžã® F Risk Alert æœ¬æ–‡ã‚’ 2-4 æ–‡ã§ä½œæˆã—ã¦ãã ã•ã„ã€‚"
        "å¿…ãšã€Žã©ã®éŽåŽ»Fæ”¯å‡ºå‰ã‚±ãƒ¼ã‚¹ã«ä¼¼ã¦ã„ã‚‹ã‹ï¼ˆæ—¥ä»˜ï¼‰ã€ã€Žä¸€è‡´è¦ç´ 1ã€œ3ä»¶ã€ã€Žã‚±ãƒ¼ã‚¹ã‚¿ã‚¤ãƒ—ã€ã‚’å«ã‚ã‚‹ã€‚"
        "similarity_score_total ã¨ final_alert_basis ã‚’è¸ã¾ãˆã€éŽå‰°ã«ç…½ã‚‰ãšå…·ä½“çš„ã«ã€‚"
        "explanation_points ã‚’æœ€å„ªå…ˆã§ä½¿ã„ã€risk_json ã«ç„¡ã„ç†ç”±ã‚’è¶³ã•ãªã„ã€‚å‡ºåŠ›ã¯æœ¬æ–‡ã®ã¿ã€‚\n"
        f"risk_json={_json_dumps(risk_json)}"
    )
    try:
        text = chat_completion(
            model=model,
            system_prompt="ã‚ãªãŸã¯F Risk Alertã‚’æ ¹æ‹ ã«å¿ å®Ÿãªæ—¥æœ¬èªžã¸æ•´å½¢ã™ã‚‹ã‚¢ã‚·ã‚¹ã‚¿ãƒ³ãƒˆã§ã™ã€‚",
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
        "data_status": "degraded",
    }
    history_days = max(60, int(os.getenv("F_RISK_HISTORY_DAYS", "365") or "365"))
    histories = _load_histories_with_bulk_fallback(daily_log_read_url=daily_log_read_url, bearer_token=bearer_token, target_date=training_end_date, days=history_days)
    histories = _hydrate_expense_f_from_expenses_db(histories)
    invalid_expense_statuses = sorted(
        {
                str(getattr(item, "expense_f_data_status", None))
                for item in histories
                if getattr(item, "expense_f_data_status", None) not in (None, "ok", "no_results")
        }
    )
    if invalid_expense_statuses:
        risk_json.update(
            {
                "skipped_reason": "expense_history_unavailable",
                "no_alert_reason": "expense_history_unavailable",
                "expense_history_statuses": invalid_expense_statuses,
                "data_status": "failed" if "query_failed" in invalid_expense_statuses else "degraded",
            }
        )
        return FRiskResult(None, None, None, [], "expense_history_unavailable", {"risk_json": risk_json})
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
    today = _build_prediction_row(train, prediction_date=prediction_date, daily_log_context_date=daily_log_context_date)
    prediction_work = work.tail(0).copy()
    try:
        import pandas as pd
        prediction_work = pd.concat([train, today], ignore_index=True)
    except Exception:
        prediction_work = train
    recent_case = build_recent_case_signature(prediction_work, pre_days=pre_days)
    similarity = compute_case_similarity(recent_case=recent_case, event_cases=event_cases, top_n=top_matches)

    pattern_summary = _explore_patterns(train)
    recent_train = train.tail(max(45, min(90, len(train))))
    availability = _build_input_availability(work)
    ml_training_days = int(len(train))
    ml_positive_event_count = int(train["f_event_flag"].sum())
    has_both_classes = int(train["f_event_flag"].nunique()) >= 2
    ml_skip_reason = None
    if ml_training_days < 30 or ml_positive_event_count < 3 or not has_both_classes:
        ml_skip_reason = "insufficient_ml_training_data"
        recent_model = {"score": None, "model": None, "skipped_reason": ml_skip_reason, "ml_training_days": ml_training_days, "ml_positive_event_count": ml_positive_event_count}
        longterm_model = {"score": None, "model": None, "skipped_reason": ml_skip_reason, "ml_training_days": ml_training_days, "ml_positive_event_count": ml_positive_event_count}
    else:
        recent_model = _fit_model(recent_train, today, sample_weight_mode="uniform")
        longterm_model = _fit_model(train, today, sample_weight_mode="recency_decay")
    fallback_used = False
    fallback_meta: dict[str, Any] = {}
    if recent_model.get("skipped_reason") and longterm_model.get("skipped_reason"):
        fallback_used = True
        fallback_meta = _rule_based_fallback(today.iloc[0].to_dict(), availability=availability)

    recent_score = _to_float(recent_model.get("score"))
    long_score = _to_float(longterm_model.get("score"))
    # Final scoring order is intentional: ML -> fallback -> final score -> level -> match.
    blended = (
        _to_float(fallback_meta.get("blended_score"))
        if fallback_used
        else _blend_scores(recent_score, long_score)
    )
    matched = _derive_matched_features(today.iloc[0].to_dict())
    if fallback_used:
        matched = list(dict.fromkeys([*matched, *[str(x) for x in fallback_meta.get("matched_factors", [])]]))
    explanation_points = _build_explanation_points(
        matched=matched,
        similarity={"summary": similarity.get("summary", ""), "strength": similarity.get("strength", "weak")},
        today=today.iloc[0].to_dict(),
    )
    evidence_sufficiency = "sufficient" if len(explanation_points) >= 2 else "limited"
    confidence = "high" if len(train) >= 60 else "medium" if len(train) >= 30 else "low"
    reliability = "high" if similarity["strength"] == "strong" else "medium" if similarity["strength"] == "medium" else "low"

    rule_meta = _score_rule_based_risk(today.iloc[0].to_dict())
    rule_count = int(rule_meta.get("score", 0))
    high_threshold = float(os.getenv("F_RISK_SIMILARITY_HIGH_THRESHOLD", "0.72") or "0.72")
    medium_threshold = float(os.getenv("F_RISK_SIMILARITY_MEDIUM_THRESHOLD", "0.55") or "0.55")
    sim_total = float(similarity.get("score_total") or 0.0)
    sim_level = str(similarity.get("strength") or "weak")
    case_high = sim_total >= high_threshold
    case_medium_plus_rule = sim_total >= medium_threshold and rule_count >= 2
    final_score = blended
    ml_probability = _blend_scores(recent_score, long_score)
    model_support = bool(ml_probability is not None and ml_probability >= 0.62)
    rule_score = rule_count
    high = bool((final_score is not None and final_score >= 0.70) or sim_total >= high_threshold or (final_score is not None and final_score >= 0.55 and rule_score >= 5))
    medium = bool((final_score is not None and final_score >= 0.40) or sim_total >= medium_threshold or rule_score >= 3)
    risk_level = "high" if high else "medium" if medium else "low"
    min_level = str(os.getenv("F_RISK_ALERT_MIN_LEVEL", "high")).strip().lower()
    risk_matched = bool(high if min_level == "high" else (high or medium))
    if fallback_used and min_level != "high":
        risk_matched = bool(risk_matched or fallback_meta.get("risk_matched"))

    prediction_feature_names = _build_xy(train.copy(), today.copy())[0].columns.tolist()
    forbidden_feature_names = [
        "spending_total", "expense_f_count", "expense_f_total", "spending_vs_7d_delta",
        "study_minutes", "study_sessions", "study_last_used_at", "study_zero_day_streak",
        "study_heavy_day_flag", "study_under_target_flag", "study_minutes_vs_7d_delta",
    ]
    forbidden_used = any(name in prediction_feature_names for name in forbidden_feature_names)
    today_for_missing = _ensure_columns(today, prediction_feature_names)
    total_feature_count = len(prediction_feature_names)
    effective_feature_count = int(today_for_missing[prediction_feature_names].notna().sum(axis=1).iloc[0]) if total_feature_count else 0
    overall_missing_rate = float(1.0 - (effective_feature_count / max(total_feature_count, 1)))
    sleep_missing_rate = _missing_rate_for_prefix(today_for_missing, prediction_feature_names, "sleep")
    notes_missing_rate = _missing_rate_for_prefix(today_for_missing, prediction_feature_names, "notes")
    meal_missing_rate = _missing_rate_for_prefix(today_for_missing, prediction_feature_names, "kcal")  # meal proxy
    location_missing_rate = _missing_rate_for_prefix(today_for_missing, prediction_feature_names, "location")
    study_missing_rate = _missing_rate_for_prefix(today_for_missing, prediction_feature_names, "study")
    if overall_missing_rate >= 0.6 or effective_feature_count < 5:
        confidence = "low"
        if final_score is not None and final_score >= 0.70 and not (rule_score >= 3 or sim_total >= medium_threshold):
            high = False
            risk_level = "medium" if medium else "low"
    # Recalculate final match decision after all quality/missingness corrections.
    if forbidden_used:
        risk_matched = False
    elif min_level == "high":
        risk_matched = bool(high)
    else:
        risk_matched = bool(high or medium)
    study_feature_names = [
        "study_minutes_lag_1",
        "study_minutes_rolling_sum_7d",
        "study_zero_day_streak",
        "study_consistency_score_7d",
    ]
    available_study_cols = [c for c in study_feature_names if c in prediction_feature_names and c in train.columns]
    study_has_values = any(train[c].notna().any() for c in available_study_cols) if available_study_cols else False
    risk_json.update(
        {
            "risk_matched": risk_matched,
            "risk_probability_recent": recent_score,
            "risk_probability_longterm": long_score,
            "recent_pattern_matches": [str(x) for x in similarity.get("matched_pre_patterns", [])[:5]],
            "f_day_similarity_summary": similarity.get("summary"),
            "matched_risk_factors": matched[:3],
            "matched_protective_factors": _derive_protective_features(today.iloc[0].to_dict())[:1],
            "matched_patterns÷Ž:¶‰žËkºwµçu•É¡…¹ÑÌˆ¤½È€ˆˆ¤¹±½Ý•È ¤4(€€€±…Ñ•}½ÕÑ¥¹œ€ô‰½½°¡•Ù•¹Ñ}‘…ä¹•Ð ‰±…Ñ•}½ÕÑ¥¹}™±…œˆ¤¤½È…¹ä¡‰½½°¡È¹•Ð ‰±…Ñ•}½ÕÑ¥¹}™±…œˆ¤¤™½ÈÈ¥¸ÁÉ•}É½ÝÌ¤4(€€€‘É¥¹­¥¹œ€ô‰½½°¡•Ù•¹Ñ}‘…ä¹•Ð ‰¹½Ñ•Í}¡…Í}‘É¥¹­¥¹œˆ¤¤½È…¹ä¡‰½½°¡È¹•Ð ‰¹½Ñ•Í}¡…Í}‘É¥¹­¥¹œˆ¤¤™½ÈÈ¥¸ÁÉ•}É½ÝÌ¤4(€€€Í½¥…±}±½…€ô‰½½°¡•Ù•¹Ñ}‘…ä¹•Ð ‰¹½Ñ•Í}Í½¥…±}±½…‘}™±…œˆ¤¤½È…¹ä¡‰½½°¡È¹•Ð ‰¹½Ñ•Í}Í½¥…±}±½…‘}™±…œˆ¤¤™½ÈÈ¥¸ÁÉ•}É½ÝÌ¤4(€€€ÍÑÉ•ÍÌ€ô‰½½°¡•Ù•¹Ñ}‘…ä¹•Ð ‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…œˆ¤¤½È…¹ä¡‰½½°¡È¹•Ð ‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…œˆ¤¤™½ÈÈ¥¸ÁÉ•}É½ÝÌ¤4(€€€‘•Ñ½ÕÈ€ô‰½½°¡•Ù•¹Ñ}‘…ä¹•Ð ‰µÕ±Ñ¥}ÍÑ½Á}™±…œˆ¤¤½È€ ‰ÍÑ…Ñ¥½¸ˆ¥¸µ•É¡…¹ÑÌ½È€‹¦žˆ¥¸µ•É¡…¹ÑÌ¤4(€€€ÍÁ•¹‘}‘•±Ñ„€ô€¡}Ñ½}™±½…Ð¡•Ù•¹Ñ}‘…ä¹•Ð ‰ÍÁ•¹‘¥¹}ÙÍ|Ý‘}‘•±Ñ„ˆ¤¤½È€À¤4(€€€¥˜±…Ñ•}½ÕÑ¥¹œ…¹Í½¥…±}±½…è4(€€€€€€€É•ÑÕÉ¸€‰¹¥¡Ñ}½ÕÑ¥¹œˆ4(€€€¥˜‘É¥¹­¥¹œ…¹Í½¥…±}±½…è4(€€€€€€€É•ÑÕÉ¸€‰‘É¥¹­¥¹}Í½¥…°ˆ4(€€€¥˜ÍÁ•¹‘}‘•±Ñ„€ø€ÌÀÀÀè4(€€€€€€€É•ÑÕÉ¸€‰¥µÁÕ±Í•}ÍÁ•¹ˆ4(€€€¥˜ÍÑÉ•ÍÌè4(€€€€€€€É•ÑÕÉ¸€‰ÍÑÉ•ÍÍ}É•±•…Í”ˆ4(€€€¥˜‘•Ñ½ÕÈè4(€€€€€€€É•ÑÕÉ¸€‰½µµÕÑ•}‘•Ñ½ÕÈˆ4(€€€É•ÑÕÉ¸€‰Õ¹­¹½Ý¸ˆ4(4(4)‘•˜}‰Õ¥±‘}É••¹Ñ}…Í”¡Ý½É¬è¹ä°€¨°‘…åÌè¥¹Ð¤€´ø‘¥ÑmÍÑÈ°¹åtè4(€€€É••¹Ñ}É½ÝÌ€ôÝ½É¬¹Ñ…¥°¡‘…åÌ¤¹Ñ½}‘¥Ð ‰É•½É‘Ìˆ¤4(€€€É•ÑÕÉ¸ì‰É••¹Ñ}É½ÝÌˆèÉ••¹Ñ}É½ÝÍô4(4(4)‘•˜}½µÁÕÑ•}…Í•}Í¥µ¥±…É¥Ñä ¨°É••¹Ñ}…Í”è‘¥ÑmÍÑÈ°¹åt°•Ù•¹Ñ}…Í•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut°Ñ½Á}¸è¥¹Ð¤€´ø‘¥ÑmÍÑÈ°¹åtè4(€€€É••¹Ñ}É½ÝÌ€ôÉ••¹Ñ}…Í”¹•Ð ‰É••¹Ñ}É½ÝÌˆ¤½Èmt4(€€€¥˜¹½Ð•Ù•¹Ñ}…Í•Ì½È¹½ÐÉ••¹Ñ}É½ÝÌè4(€€€€€€€É•ÑÕÉ¸ì4(€€€€€€€€€€€€‰ÍÑÉ•¹Ñ ˆè€‰Ý•…¬ˆ°4(€€€€€€€€€€€€‰ÍÕµµ…Éäˆè€‹¦;–:íŽ
ÇŽóŽ
çŽ£Ž»š¾S¢ò–¾û¢Æ‡Ž3’â7¢ÚÌˆ°4(€€€€€€€€€€€€‰Ñ½Á}…Í•}µ…Ñ¡•Ìˆèmt°4(€€€€€€€€€€€€‰Ñ½Á}…Í•}µ…Ñ¡}Í½É•Ìˆèmt°4(€€€€€€€€€€€€‰µ…Ñ¡•‘}…Í•}‘…Ñ•Ìˆèmt°4(€€€€€€€€€€€€‰µ…Ñ¡•‘}…Í•}ÑåÁ•Ìˆèmt°4(€€€€€€€€€€€€‰µ…Ñ¡•‘}ÁÉ•}Á…ÑÑ•É¹Ìˆèmt°4(€€€€€€€€€€€€‰Í½É•}Ñ½Ñ…°ˆè€À¸À°4(€€€€€€€€€€€€‰Í½É•}½Ù•É±…Àˆè€À¸À°4(€€€€€€€€€€€€‰Í½É•}Í•ÅÕ•¹”ˆè€À¸À°4(€€€€€€€€€€€€‰Í½É•}ÑåÁ”ˆè€À¸À°4(€€€€€€€€€€€€‰ÕÍ…‰±•}™}•Ù•¹Ñ}½Õ¹Ðˆè€À°4(€€€€€€€ô4(€€€Í½É•è±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt4(€€€™½È…Í”¥¸•Ù•¹Ñ}…Í•Ìè4(€€€€€€€ÁÉ•}É½ÝÌ€ô…Í”¹•Ð ‰ÁÉ•}É½ÝÌˆ¤½Èmt4(€€€€€€€¥˜¹½ÐÁÉ•}É½ÝÌè4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€½Ù•É±…À€ô}™•…ÑÕÉ•}½Ù•É±…Á}Í½É”¡É••¹Ñ}É½ÝÌ°ÁÉ•}É½ÝÌ¤4(€€€€€€€Í•ÅÕ•¹”€ô}Ñ•µÁ½É…±}Í•ÅÕ•¹•}Í½É”¡É••¹Ñ}É½ÝÌ°ÁÉ•}É½ÝÌ¤4(€€€€€€€ÑåÁ•}Í½É”€ô}•Ù•¹Ñ}ÑåÁ•}½¹Í¥ÍÑ•¹å}Í½É”¡É••¹Ñ}É½ÝÌ°ÍÑÈ¡…Í”¹•Ð ‰•Ù•¹Ñ}ÑåÁ”ˆ¤½È€‰Õ¹­¹½Ý¸ˆ¤¤4(€€€€€€€Ñ½Ñ…°€ôÉ½Õ¹  À¸Ô€¨½Ù•É±…À¤€¬€ À¸ÌÔ€¨Í•ÅÕ•¹”¤€¬€ À¸ÄÔ€¨ÑåÁ•}Í½É”¤°€Ì¤4(€€€€€€€Í½É•¹…ÁÁ•¹¡ì4(€€€€€€€€€€€€‰•Ù•¹Ñ}‘…Ñ”ˆè…Í”¹•Ð ‰•Ù•¹Ñ}‘…Ñ”ˆ¤°4(€€€€€€€€€€€€‰•Ù•¹Ñ}ÑåÁ”ˆè…Í”¹•Ð ‰•Ù•¹Ñ}ÑåÁ”ˆ¤°4(€€€€€€€€€€€€‰Í½É•}Ñ½Ñ…°ˆèÑ½Ñ…°°4(€€€€€€€€€€€€‰Í½É•}½Ù•É±…Àˆè½Ù•É±…À°4(€€€€€€€€€€€€‰Í½É•}Í•ÅÕ•¹”ˆèÍ•ÅÕ•¹”°4(€€€€€€€€€€€€‰Í½É•}ÑåÁ”ˆèÑåÁ•}Í½É”°4(€€€€€€€ô¤4(€€€¥˜¹½ÐÍ½É•è4(€€€€€€€É•ÑÕÉ¸ì4(€€€€€€€€€€€€‰ÍÑÉ•¹Ñ ˆè€‰Ý•…¬ˆ°4(€€€€€€€€€€€€‰ÍÕµµ…Éäˆè€‹š¾S¢ò–>¿¢÷Ž«¦;–:íÁÉ”µÝ¥¹‘½ßŽ3’â7¢ÚÌˆ°4(€€€€€€€€€€€€‰Ñ½Á}…Í•}µ…Ñ¡•Ìˆèmt°4(€€€€€€€€€€€€‰Ñ½Á}…Í•}µ…Ñ¡}Í½É•Ìˆèmt°4(€€€€€€€€€€€€‰µ…Ñ¡•‘}…Í•}‘…Ñ•Ìˆèmt°4(€€€€€€€€€€€€‰µ…Ñ¡•‘}…Í•}ÑåÁ•Ìˆèmt°4(€€€€€€€€€€€€‰µ…Ñ¡•‘}ÁÉ•}Á…ÑÑ•É¹Ìˆèmt°4(€€€€€€€€€€€€‰Í½É•}Ñ½Ñ…°ˆè€À¸À°4(€€€€€€€€€€€€‰Í½É•}½Ù•É±…Àˆè€À¸À°4(€€€€€€€€€€€€‰Í½É•}Í•ÅÕ•¹”ˆè€À¸À°4(€€€€€€€€€€€€‰Í½É•}ÑåÁ”ˆè€À¸À°4(€€€€€€€€€€€€‰ÕÍ…‰±•}™}•Ù•¹Ñ}½Õ¹Ðˆè€À°4(€€€€€€€ô4(€€€Í½É•€ôÍ½ÉÑ•¡Í½É•°­•äõ±…µ‰‘„àèál‰Í½É•}Ñ½Ñ…°‰t°É•Ù•ÉÍ”õQÉÕ”¤4(€€€Ñ½À€ôÍ½É•‘léÑ½Á}¹t4(€€€‰•ÍÐ€ôÑ½ÁlÁt4(€€€ÍÑÉ•¹Ñ €ô€‰ÍÑÉ½¹œˆ¥˜‰•ÍÑl‰Í½É•}Ñ½Ñ…°‰t€øô€À¸ÜÈ•±Í”€‰µ•‘¥Õ´ˆ¥˜‰•ÍÑl‰Í½É•}Ñ½Ñ…°‰t€øô€À¸ÔÔ•±Í”€‰Ý•…¬ˆ4(€€€µ…Ñ¡•‘}Á…ÑÑ•É¹Ì€ô}‘•ÍÉ¥‰•}µ…Ñ¡•‘}Á…ÑÑ•É¹Ì¡É••¹Ñ}É½ÝÌ¤4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰ÍÑÉ•¹Ñ ˆèÍÑÉ•¹Ñ °4(€€€€€€€€‰ÍÕµµ…Éäˆè˜‹žnÓ¢þEí±•¸¡É••¹Ñ}É½ÝÌ¥÷š^—Ž»’â›ŽÏŽ¿¦;–:í–&7–Ž¡íÍÑÉ•¹Ñ¡÷’â¢Ó¾ò#šr¢&¿’â¢Ðí‰•ÍÑl•Ù•¹Ñ}‘…Ñ”u÷¾ò$ˆ°4(€€€€€€€€‰Ñ½Á}…Í•}µ…Ñ¡•ÌˆèÑ½À°4(€€€€€€€€‰Ñ½Á}…Í•}µ…Ñ¡}Í½É•Ìˆèmál‰Í½É•}Ñ½Ñ…°‰t™½Èà¥¸Ñ½Át°4(€€€€€€€€‰µ…Ñ¡•‘}…Í•}‘…Ñ•ÌˆèmÍÑÈ¡ál‰•Ù•¹Ñ}‘…Ñ”‰t¤™½Èà¥¸Ñ½Át°4(€€€€€€€€‰µ…Ñ¡•‘}…Í•}ÑåÁ•ÌˆèmÍÑÈ¡ál‰•Ù•¹Ñ}ÑåÁ”‰t¤™½Èà¥¸Ñ½Át°4(€€€€€€€€‰µ…Ñ¡•‘}ÁÉ•}Á…ÑÑ•É¹Ìˆèµ…Ñ¡•‘}Á…ÑÑ•É¹Ì°4(€€€€€€€€‰Í½É•}Ñ½Ñ…°ˆè‰•ÍÑl‰Í½É•}Ñ½Ñ…°‰t°4(€€€€€€€€‰Í½É•}½Ù•É±…Àˆè‰•ÍÑl‰Í½É•}½Ù•É±…À‰t°4(€€€€€€€€‰Í½É•}Í•ÅÕ•¹”ˆè‰•ÍÑl‰Í½É•}Í•ÅÕ•¹”‰t°4(€€€€€€€€‰Í½É•}ÑåÁ”ˆè‰•ÍÑl‰Í½É•}ÑåÁ”‰t°4(€€€€€€€€‰ÕÍ…‰±•}™}•Ù•¹Ñ}½Õ¹Ðˆè±•¸¡Í½É•¤°4(€€€ô4(4(4)‘•˜}™•…ÑÕÉ•}½Ù•É±…Á}Í½É”¡É••¹Ñ}É½ÝÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut°ÁÉ•}É½ÝÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut¤€´ø™±½…Ðè4(€€€­•åÌ€ôl4(€€€€€€€€‰Í±••Á}Í¡½ÉÑ}ÍÑÉ•…¬ˆ°€‰‰•‘Ñ¥µ•}µ¥¸ˆ°€‰Í±••Á}Í½É”ˆ°€‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…œˆ°€‰¹½Ñ•Í}™…Ñ¥Õ•}™±…œˆ°€‰¹½Ñ•Í}Í½¥…±}±½…‘}™±…œˆ°4(€€€€€€€€‰¹½Ñ•Í}Í±••Á}¥ÍÍÕ•}™±…œˆ°€‰¹½Ñ•Í}¡…Í}‘É¥¹­¥¹œˆ°€‰¹½Ñ•Í}¡…Í}±…Ñ•}Ý½É¬ˆ°€‰¹½Ñ•Í}¡…Í}Í½¥…°ˆ°€‰¹½Ñ•Í}¡…Í}É•É•Ðˆ°€‰¹½Ñ•Í}¡…Í}½¹™±¥Ðˆ°4(€€€€€€€€‰±…Ñ•}½ÕÑ¥¹}™±…œˆ°€‰µÕ±Ñ¥}ÍÑ½Á}™±…œˆ°€‰½ÕÑ¥¹}¡•…Ùå}™±…œˆ°€‰¡½µ•}¡•…Ùå}™±…œˆ°€‰ÍÁ•¹‘¥¹}ÙÍ|Ý‘}‘•±Ñ„ˆ°€‰Í½¥…±}ÍÁ•¹‘}±¥­•}™±…œˆ°4(€€€€€€€€‰½¹Ù•¹¥•¹•}ÍÑ½É•}±¥­•}™±…œˆ°€‰­…±}ÙÍ|Ý‘}‘•±Ñ„ˆ°€‰™…Ñ}ÙÍ|Ý‘}‘•±Ñ„ˆ°€‰…É‰}ÙÍ|Ý‘}‘•±Ñ„ˆ°€‰¡¥¡}™…Ñ}™±…œˆ°€‰¡¥¡}…É‰}™±…œˆ°4(€€€€€€€€‰Ñ…Í­}½µÁ±•Ñ¥½¹}É…Ñ¥¼ˆ°€‰‘½¹•}ÙÍ|Ý‘}‘•±Ñ„ˆ°€‰‘É½Á}ÙÍ|Ý‘}‘•±Ñ„ˆ°€‰Í¡•‘Õ±•}Í…µ•}‘…å}•Ù•¹Ñ}½Õ¹Ðˆ°€‰±…Ñ•}•Ù•¹Ñ}±¥­•}™±…œˆ°4(€€€€€€€€‰Ý•…Ñ¡•É}‰…‘}™±…œˆ°€‰Ý•…Ñ¡•É}ÁÉ•¥Á}ÁÉ½‰…‰¥±¥Ñå}µ…àˆ°€‰Ý•…Ñ¡•É}Ñ•µÁ}É…¹•}Œˆ°4(€€€t4(€€€Ñ½Ñ…°€ô€À¸À4(€€€µ…Ñ¡•€ô€À¸À4(€€€™½È­•ä¥¸­•åÌè4(€€€€€€€É••¹Ñ}Ø€ô}Ñ½}™±½…Ð¡É••¹Ñ}É½ÝÍl´Åt¹•Ð¡­•ä¤¤¥˜É••¹Ñ}É½ÝÌ•±Í”9½¹”4(€€€€€€€ÁÉ•}Ø€ô}Ñ½}™±½…Ð¡ÁÉ•}É½ÝÍl´Åt¹•Ð¡­•ä¤¤¥˜ÁÉ•}É½ÝÌ•±Í”9½¹”4(€€€€€€€¥˜É••¹Ñ}Ø¥Ì9½¹”…¹ÁÉ•}Ø¥Ì9½¹”è4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€¥˜€¡É••¹Ñ}Ø¥Ì9½¹”½È…‰Ì¡É••¹Ñ}Ø¤€ð€À¸ÀÔ¤…¹€¡ÁÉ•}Ø¥Ì9½¹”½È…‰Ì¡ÁÉ•}Ø¤€ð€À¸ÀÔ¤è4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€Ñ½Ñ…°€¬ô€Ä¸À4(€€€€€€€¥˜É••¹Ñ}Ø¥Ì9½¹”½ÈÁÉ•}Ø¥Ì9½¹”è4(€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€¥˜…‰Ì¡É••¹Ñ}Ø€´ÁÉ•}Ø¤€ðôµ…à À¸È°…‰Ì¡ÁÉ•}Ø¤€¨€À¸ÌÔ¤è4(€€€€€€€€€€€µ…Ñ¡•€¬ô€Ä¸À4(€€€¥˜Ñ½Ñ…°€ðô€Àè4(€€€€€€€É•ÑÕÉ¸€À¸À4(€€€É•ÑÕÉ¸É½Õ¹¡µ…Ñ¡•€¼Ñ½Ñ…°°€Ì¤4(4(4)‘•˜}Ñ•µÁ½É…±}Í•ÅÕ•¹•}Í½É”¡É••¹Ñ}É½ÝÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut°ÁÉ•}É½ÝÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut¤€´ø™±½…Ðè4(€€€Í¥é”€ôµ¥¸¡±•¸¡É••¹Ñ}É½ÝÌ¤°±•¸¡ÁÉ•}É½ÝÌ¤¤4(€€€¥˜Í¥é”€ðô€Àè4(€€€€€€€É•ÑÕÉ¸€À¸À4(€€€Í½É”€ô€À¸À4(€€€™½È¤¥¸É…¹” Ä°Í¥é”€¬€Ä¤è4(€€€€€€€É••¹Ð€ôÉ••¹Ñ}É½ÝÍlµ¥t4(€€€€€€€ÁÉ”€ôÁÉ•}É½ÝÍlµ¥t4(€€€€€€€É••¹Ñ}Ý•¥¡Ð€ô€Ä¸À€¬€ À¸ÈÔ¥˜¤€ôô€Ä•±Í”€À¸À¤4(€€€€€€€Í¡½ÉÑ}Í±••Á}µ…Ñ €ô‰½½° ¡}Ñ½}™±½…Ð¡É••¹Ð¹•Ð ‰Í±••Á}Í¡½ÉÑ}ÍÑÉ•…¬ˆ¤¤½È€À¤€øô€È¤…¹‰½½° ¡}Ñ½}™±½…Ð¡ÁÉ”¹•Ð ‰Í±••Á}Í¡½ÉÑ}ÍÑÉ•…¬ˆ¤¤½È€À¤€øô€È¤4(€€€€€€€ÍÑÉ•ÍÍ}µ…Ñ €ô‰½½°¡É••¹Ð¹•Ð ‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…œˆ¤¤…¹‰½½°¡ÁÉ”¹•Ð ‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…œˆ¤¤4(€€€€€€€±…Ñ•}½ÕÑ¥¹}µ…Ñ €ô‰½½°¡É••¹Ð¹•Ð ‰±…Ñ•}½ÕÑ¥¹}™±…œˆ¤¤…¹‰½½°¡ÁÉ”¹•Ð ‰±…Ñ•}½ÕÑ¥¹}™±…œˆ¤¤4(€€€€€€€Í½É”€¬ôÉ••¹Ñ}Ý•¥¡Ð€¨€¡™±½…Ð¡Í¡½ÉÑ}Í±••Á}µ…Ñ ¤€¬™±½…Ð¡ÍÑÉ•ÍÍ}µ…Ñ ¤€¬™±½…Ð¡±…Ñ•}½ÕÑ¥¹}µ…Ñ ¤¤€¼€Ì¸À4(€€€‘•¹½´€ôÍÕ´ Ä¸À€¬€ À¸ÈÔ¥˜¤€ôô€Ä•±Í”€À¸À¤™½È¤¥¸É…¹” Ä°Í¥é”€¬€Ä¤¤4(€€€É•ÑÕÉ¸É½Õ¹¡Í½É”€¼µ…à¡‘•¹½´°€Ä¸À¤°€Ì¤4(4(4)‘•˜}•Ù•¹Ñ}ÑåÁ•}½¹Í¥ÍÑ•¹å}Í½É”¡É••¹Ñ}É½ÝÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut°•Ù•¹Ñ}ÑåÁ”èÍÑÈ¤€´ø™±½…Ðè4(€€€±…Ñ•ÍÐ€ôÉ••¹Ñ}É½ÝÍl´Åt¥˜É••¹Ñ}É½ÝÌ•±Í”íô4(€€€¥˜•Ù•¹Ñ}ÑåÁ”€ôô€‰¹¥¡Ñ}½ÕÑ¥¹œˆè4(€€€€€€€É•ÑÕÉ¸€Ä¸À¥˜‰½½°¡±…Ñ•ÍÐ¹•Ð ‰±…Ñ•}½ÕÑ¥¹}™±…œˆ¤¤•±Í”€À¸ÌÔ4(€€€¥˜•Ù•¹Ñ}ÑåÁ”€ôô€‰‘É¥¹­¥¹}Í½¥…°ˆè4(€€€€€€€É•ÑÕÉ¸€Ä¸À¥˜‰½½°¡±…Ñ•ÍÐ¹•Ð ‰¹½Ñ•Í}¡…Í}‘É¥¹­¥¹œˆ¤¤…¹‰½½°¡±…Ñ•ÍÐ¹•Ð ‰¹½Ñ•Í}Í½¥…±}±½…‘}™±…œˆ¤¤•±Í”€À¸ÈÔ4(€€€¥˜•Ù•¹Ñ}ÑåÁ”€ôô€‰¥µÁÕ±Í•}ÍÁ•¹ˆè4(€€€€€€€É•ÑÕÉ¸€Ä¸À¥˜€¡}Ñ½}™±½…Ð¡±…Ñ•ÍÐ¹•Ð ‰ÍÁ•¹‘¥¹}ÙÍ|Ý‘}‘•±Ñ„ˆ¤¤½È€À¤€ø€ÈÀÀÀ•±Í”€À¸Ì4(€€€¥˜•Ù•¹Ñ}ÑåÁ”€ôô€‰ÍÑÉ•ÍÍ}É•±•…Í”ˆè4(€€€€€€€É•ÑÕÉ¸€Ä¸À¥˜‰½½°¡±…Ñ•ÍÐ¹•Ð ‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…œˆ¤¤•±Í”€À¸Ì4(€€€¥˜•Ù•¹Ñ}ÑåÁ”€ôô€‰½µµÕÑ•}‘•Ñ½ÕÈˆè4(€€€€€€€É•ÑÕÉ¸€Ä¸À¥˜‰½½°¡±…Ñ•ÍÐ¹•Ð ‰µÕ±Ñ¥}ÍÑ½Á}™±…œˆ¤¤•±Í”€À¸Ì4(€€€É•ÑÕÉ¸€À¸È4(4(4)‘•˜}‘•ÍÉ¥‰•}µ…Ñ¡•‘}Á…ÑÑ•É¹Ì¡É••¹Ñ}É½ÝÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut¤€´ø±¥ÍÑmÍÑÉtè4(€€€¥˜¹½ÐÉ••¹Ñ}É½ÝÌè4(€€€€€€€É•ÑÕÉ¸mt4(€€€±…Ñ•ÍÐ€ôÉ••¹Ñ}É½ÝÍl´Åt4(€€€½ÕÐè±¥ÍÑmÍÑÉt€ômt4(€€€¥˜€¡}Ñ½}™±½…Ð¡±…Ñ•ÍÐ¹•Ð ‰Í±••Á}Í¡½ÉÑ}ÍÑÉ•…¬ˆ¤¤½È€À¤€øô€Èè4(€€€€€€€½ÕÐ¹…ÁÁ•¹ ‹ž~·žv‡žrƒ¦žÚhˆ¤4(€€€¥˜‰½½°¡±…Ñ•ÍÐ¹•Ð ‰¹½Ñ•Í}Í½¥…±}±½…‘}™±…œˆ¤¤è4(€€€€€€€½ÕÐ¹…ÁÁ•¹ ‰Í½¥…°±½…ˆ¤4(€€€¥˜‰½½°¡±…Ñ•ÍÐ¹•Ð ‰±…Ñ•}½ÕÑ¥¹}™±…œˆ¤¤è4(€€€€€€€½ÕÐ¹…ÁÁ•¹ ‹–’s–’[–ë–
û–BDˆ¤4(€€€¥˜‰½½°¡±…Ñ•ÍÐ¹•Ð ‰¹½Ñ•Í}¡…Í}‘É¥¹­¥¹œˆ¤¤è4(€€€€€€€½ÕÐ¹…ÁÁ•¹ ‰‘É¥¹­¥¹œˆ¤4(€€€¥˜‰½½°¡±…Ñ•ÍÐ¹•Ð ‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…œˆ¤¤è4(€€€€€€€½ÕÐ¹…ÁÁ•¹ ‰ÍÑÉ•ÍÌˆ¤4(€€€É•ÑÕÉ¸½ÕÑlèÕt4(4(4)‘•˜}½µÁ½Í•}…Í•}…±•ÉÑ}Ñ•áÐ¡É¥Í­}©Í½¸è‘¥ÑmÍÑÈ°¹åt¤€´ø=ÁÑ¥½¹…±mÍÑÉtè4(€€€¥˜¹½ÐÉ¥Í­}©Í½¸¹•Ð ‰É¥Í­}µ…Ñ¡•ˆ¤è4(€€€€€€€É•ÑÕÉ¸9½¹”4(€€€Á½¥¹ÑÌ€ôÉ¥Í­}©Í½¸¹•Ð ‰•áÁ±…¹…Ñ¥½¹}Á½¥¹ÑÌˆ¤½Èmt4(€€€‰…Í¥Ì€ô€‹Žˆ¹©½¥¸¡ÍÑÈ¡à¤™½Èà¥¸Á½¥¹ÑÍlèÍt¤¥˜Á½¥¹ÑÌ•±Í”€‹ž~·žv‡žrƒŽïŽ
çŽ#Ž³Ž
çŽï¦;–:ïŽ
ÇŽóŽ
ç¦†{’òðˆ4(€€€É•ÑÕÉ¸€ 4(€€€€€€€€‹’î+š^—Ž½Ž«Ž
çŽ
¿Ž3¦®cŽ
ŽŸŽgŽˆ4(€€€€€€€˜‹š‚çš.ƒŽ¿Ží‰…Í¥Í÷ŽŸŽgŽˆ4(€€€€€€€€‹’î+š^—Ž¿–’ŸŽ7Ž«šR¿–ë–"“šZ·Ž–¾Ž
+¦OŽ–’sŽ»–’[–ëŽŽ
ÏŽÏŽOŽ/Žï–’[¦ŽŽ»¢þ÷–*ƒ¢Îó–—Ž
K¦ÿŽGŽ›Ž?ŽƒŽWŽŽˆ4(€€€€¤4(4(4)‘•˜}‰Õ¥±‘}ÁÉ•‘¥Ñ¥½¹}É½Ü¡ÑÉ…¥¸è¹ä°€¨°ÁÉ•‘¥Ñ¥½¹}‘…Ñ”èÍÑÈ°‘…¥±å}±½}½¹Ñ•áÑ}‘…Ñ”èÍÑÈ¤€´ø¹äè4(€€€€Œƒ’ê#šâ³–¾û¢Æ‡š^—Ž»–öOš^—–ºžâûŽ
K’öÿŽ
?Ž«ŽŽŽ
Ž–¶›žþKšr¯–Âû¾ò#š^—š²‡ŽŸ¦k–âãŽ¿šb£š^—¾ò'Ž»¢†3Ž
K–r–>ÃŽ¬4(€€€€ŒÁÉ•‘¥Ñ¥½¹}‘…Ñ”ƒŽ
K’â;Ž#ŽŽ3’î+š^—’ê#šâ³žR É½ßŽ7Ž
K’ösŽ
/ŽŽOŽ
3Ž¿’ê/–ú3š–‚ÇŽ«ŽóŽ
¿¦bËš¶‹Ž»ŽŽ
Ž4(€€€É½Ü€ôÑÉ…¥¸¹Ñ…¥° Ä¤¹½Áä ¤4(€€€¥˜±•¸¡É½Ü¤€ôô€Àè4(€€€€€€€É•ÑÕÉ¸ÑÉ…¥¸¹Ñ…¥° À¤¹½Áä ¤4(€€€É½Ü¹±½lè°€‰‘…Ñ”‰t€ôÁÉ•‘¥Ñ¥½¹}‘…Ñ”4(€€€É½Ü¹±½lè°€‰¥Í}Ý••­•¹‰t€ô€Ä¥˜‘…Ñ•Ñ¥µ”¹ÍÑÉÁÑ¥µ”¡ÁÉ•‘¥Ñ¥½¹}‘…Ñ”°€ˆ•d´•´´•ˆ¤¹Ý••­‘…ä ¤€øô€Ô•±Í”€À4(€€€™½È™½É‰¥‘‘•¸¥¸€ ‰ÍÁ•¹‘¥¹}Ñ½Ñ…°ˆ°€‰•áÁ•¹Í•}™}½Õ¹Ðˆ°€‰•áÁ•¹Í•}™}Ñ½Ñ…°ˆ°€‰ÍÁ•¹‘¥¹}ÙÍ|Ý‘}‘•±Ñ„ˆ¤è4(€€€€€€€¥˜™½É‰¥‘‘•¸¥¸É½Ü¹½±Õµ¹Ìè4(€€€€€€€€€€€É½Ü¹±½lè°™½É‰¥‘‘•¹t€ô9½¹”4(€€€É•ÑÕÉ¸É½Ü4(4(4)‘•˜}‰Õ¥±‘}•áÁ±…¹…Ñ¥½¹}Á½¥¹ÑÌ ¨°µ…Ñ¡•è±¥ÍÑmÍÑÉt°Í¥µ¥±…É¥Ñäè‘¥ÑmÍÑÈ°¹åt°Ñ½‘…äè‘¥ÑmÍÑÈ°¹åt¤€´ø±¥ÍÑmÍÑÉtè4(€€€Á½¥¹ÑÌè±¥ÍÑmÍÑÉt€ômt4(€€€¥˜Í¥µ¥±…É¥Ñä¹•Ð ‰ÍÕµµ…Éäˆ¤è4(€€€€€€€Á½¥¹ÑÌ¹…ÁÁ•¹¡ÍÑÈ¡Í¥µ¥±…É¥Ñål‰ÍÕµµ…Éä‰t¤¤4(€€€¥˜µ…Ñ¡•è4(€€€€€€€Á½¥¹ÑÌ¹…ÁÁ•¹¡˜‹’â¢ÓŽ_Ž’âï¢š–n€èìœ°€œ¹©½¥¸¡µ…Ñ¡•‘lèÍt¥ôˆ¤4(€€€¥˜}Ñ½}™±½…Ð¡Ñ½‘…ä¹•Ð ‰Í±••Á}Í¡½ÉÑ}ÍÑÉ•…¬ˆ¤¤…¹}Ñ½}™±½…Ð¡Ñ½‘…ä¹•Ð ‰Í±••Á}Í¡½ÉÑ}ÍÑÉ•…¬ˆ¤¤€øô€Èè4(€€€€€€€Á½¥¹ÑÌ¹…ÁÁ•¹ ‹žnÓ¢þGšVÃš^—ŽŸž~·žv‡žrƒŽ3žÚkŽ7Ž–"“šZ·¢Êƒ¢6ßŽ3’â+Ž3Ž
+Ž
ŽgŽ’â›ŽÌˆ¤4(€€€ÁÉ½Ñ•Ñ¥Ù”€ô}‘•É¥Ù•}ÁÉ½Ñ•Ñ¥Ù•}™•…ÑÕÉ•Ì¡Ñ½‘…ä¤4(€€€¥˜ÁÉ½Ñ•Ñ¥Ù”è4(€€€€€€€Á½¥¹ÑÌ¹…ÁÁ•¹¡˜‹’þw¢¶ß¢š–n€èíÁÉ½Ñ•Ñ¥Ù•lÁuôˆ¤4(€€€É•ÑÕÉ¸Á½¥¹ÑÍlèÑt4(4(4)‘•˜}‰±•¹‘}Í½É•Ì¡É••¹Ðè=ÁÑ¥½¹…±m™±½…Ñt°±½¹}Ñ•É´è=ÁÑ¥½¹…±m™±½…Ñt¤€´ø=ÁÑ¥½¹…±m™±½…Ñtè4(€€€¥˜É••¹Ð¥Ì9½¹”…¹±½¹}Ñ•É´¥Ì9½¹”è4(€€€€€€€É•ÑÕÉ¸9½¹”4(€€€¥˜É••¹Ð¥Ì9½¹”è4(€€€€€€€É•ÑÕÉ¸±½¹}Ñ•É´4(€€€¥˜±½¹}Ñ•É´¥Ì9½¹”è4(€€€€€€€É•ÑÕÉ¸É••¹Ð4(€€€É•ÑÕÉ¸€À¸Ø€¨É••¹Ð€¬€À¸Ð€¨±½¹}Ñ•É´4(4(4)‘•˜}Ñ½}™±½…Ð¡Ù…±Õ”è½‰©•Ð¤€´ø=ÁÑ¥½¹…±m™±½…Ñtè4(€€€¥˜Ù…±Õ”¥Ì9½¹”½È¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°‰½½°¤è4(€€€€€€€É•ÑÕÉ¸9½¹”4(€€€ÑÉäè4(€€€€€€€É•ÑÕÉ¸™±½…Ð¡Ù…±Õ”¤4(€€€•á•ÁÐ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€É•ÑÕÉ¸9½¹”4(4(4)‘•˜}µ¥ÍÍ¥¹}É…Ñ•}™½É}ÁÉ•™¥à¡Ñ½‘…å}É½Üè¹ä°™•…ÑÕÉ•}¹…µ•Ìè±¥ÍÑmÍÑÉt°ÁÉ•™¥àèÍÑÈ¤€´ø™±½…Ðè4(€€€­•åÌ€ôm¹…µ”™½È¹…µ”¥¸™•…ÑÕÉ•}¹…µ•Ì¥˜¹…µ”¹ÍÑ…ÉÑÍÝ¥Ñ ¡ÁÉ•™¥à¥t4(€€€¥˜¹½Ð­•åÌè4(€€€€€€€É•ÑÕÉ¸€Ä¸À4(€€€Í…™”€ô}•¹ÍÕÉ•}½±Õµ¹Ì¡Ñ½‘…å}É½Ü°­•åÌ¤4(€€€É½Ü€ôÍ…™•m­•åÍt4(€€€É•ÑÕÉ¸™±½…Ð¡É½Ü¹¥Í¹„ ¤¹µ•…¸¡…á¥ÌôÄ¤¹¥±½lÁt¤4(4(4)‘•˜}•¹ÍÕÉ•}½±Õµ¹Ì¡‘˜è¹ä°½±Õµ¹Ìè±¥ÍÑmÍÑÉt¤€´ø¹äè4(€€€½ÕÐ€ô‘˜¹½Áä ¤4(€€€™½È½°¥¸½±Õµ¹Ìè4(€€€€€€€¥˜½°¹½Ð¥¸½ÕÐ¹½±Õµ¹Ìè4(€€€€€€€€€€€½ÕÑm½±t€ô9½¹”4(€€€É•ÑÕÉ¸½ÕÐ4(4(4)‘•˜}‰Õ¥±‘}¥¹ÁÕÑ}…Ù…¥±…‰¥±¥Ñä¡Ý½É¬è¹ä¤€´ø‘¥ÑmÍÑÈ°¹åtè4(€€€±…Ñ•ÍÐ€ôÝ½É¬¹¥±½l´Åt¹Ñ½}‘¥Ð ¤¥˜±•¸¡Ý½É¬¤•±Í”íô4(€€€É½ÕÁÌ€ôì4(€€€€€€€€‰Í±••Àˆèl‰Í±••Á}¡½ÕÉÌˆ°€‰Í±••Á}Í½É”‰t°4(€€€€€€€€‰µ•…°ˆèl‰­…°ˆ°€‰ÁÉ½Ñ•¥¸ˆ°€‰™…Ðˆ°€‰…Éˆ‰t°4(€€€€€€€€‰ÍÁ•¹‘¥¹œˆèl‰ÍÁ•¹‘¥¹}Ñ½Ñ…°ˆ°€‰•áÁ•¹Í•}™}½Õ¹Ðˆ°€‰•áÁ•¹Í•}™}Ñ½Ñ…°‰t°4(€€€€€€€€‰Ñ…Í­Ìˆèl‰Ñ…Í­}‘½¹•}½Õ¹Ðˆ°€‰Ñ…Í­}‘É½Á}½Õ¹Ðˆ°€‰Ñ…Í­}½µÁ±•Ñ¥½¹}É…Ñ¥¼‰t°4(€€€€€€€€‰¹½Ñ•Ìˆèl‰¹½Ñ•Í}ÁÉ•Í•¹Ñ}™±…œˆ°€‰¹½Ñ•Í}Í¥¹…±}½Õ¹Ð‰t°4(€€€€€€€€‰±½…Ñ¥½¸ˆèl‰±½…Ñ¥½¹}ÁÉ•Í•¹Ñ}™±…œˆ°€‰±…Ñ•}½ÕÑ¥¹}™±…œˆ°€‰µÕ±Ñ¥}ÍÑ½Á}™±…œ‰t°4(€€€€€€€€‰Ý•…Ñ¡•Èˆèl‰Ý•…Ñ¡•É}É•ÑÉ¥•Ù•‘}™±…œˆ°€‰Ý•…Ñ¡•É}½‘”ˆ°€‰Ý•…Ñ¡•É}ÁÉ•¥Á}ÁÉ½‰…‰¥±¥Ñå}µ…à‰t°4(€€€€€€€€‰Í¡•‘Õ±”ˆèl‰Í¡•‘Õ±•}Í¥¹…±}…Ù…¥±…‰±•}™±…œˆ°€‰Í¡•‘Õ±•}Í…µ•}‘…å}•Ù•¹Ñ}½Õ¹Ð‰t°4(€€€ô4(€€€…Ù…¥±…‰±”è±¥ÍÑmÍÑÉt€ômt4(€€€Õ¹…Ù…¥±…‰±”è±¥ÍÑmÍÑÉt€ômt4(€€€•á±Õ‘•‘}É•…Í½¹Ìè‘¥ÑmÍÑÈ°ÍÑÉt€ôíô4(€€€½Õ¹ÑÌè‘¥ÑmÍÑÈ°¥¹Ñt€ôíô4(€€€™½ÈÉ½ÕÀ°­•åÌ¥¸É½ÕÁÌ¹¥Ñ•µÌ ¤è4(€€€€€€€É½ÕÁ}…Ù…¥±…‰±”€ô€À4(€€€€€€€™½È­•ä¥¸­•åÌè4(€€€€€€€€€€€Ù…±Õ”€ô±…Ñ•ÍÐ¹•Ð¡­•ä¤4(€€€€€€€€€€€¥˜Ù…±Õ”¥Ì9½¹”è4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°™±½…Ð¤…¹µ…Ñ ¹¥Í¹…¸¡Ù…±Õ”¤è4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°‰½½°¤è4(€€€€€€€€€€€€€€€¥˜Ù…±Õ”è4(€€€€€€€€€€€€€€€€€€€É½ÕÁ}…Ù…¥±…‰±”€¬ô€Ä4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€É½ÕÁ}…Ù…¥±…‰±”€¬ô€Ä4(€€€€€€€½Õ¹ÑÍmÉ½ÕÁt€ôÉ½ÕÁ}…Ù…¥±…‰±”4(€€€€€€€¥˜É½ÕÁ}…Ù…¥±…‰±”€ø€Àè4(€€€€€€€€€€€…Ù…¥±…‰±”¹…ÁÁ•¹¡É½ÕÀ¤4(€€€€€€€•±Í”è4(€€€€€€€€€€€Õ¹…Ù…¥±…‰±”¹…ÁÁ•¹¡É½ÕÀ¤4(€€€€€€€€€€€•á±Õ‘•‘}É•…Í½¹ÍmÉ½ÕÁt€ô€‰Õ¹…Ù…¥±…‰±•}™É½µ}•á¥ÍÑ¥¹}É•…‘}Á…Ñ ˆ4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰…Ù…¥±…‰±•}É½ÕÁÌˆè…Ù…¥±…‰±”°4(€€€€€€€€‰Õ¹…Ù…¥±…‰±•}É½ÕÁÌˆèÕ¹…Ù…¥±…‰±”°4(€€€€€€€€‰•á±Õ‘•‘}É•…Í½¹Ìˆè•á±Õ‘•‘}É•…Í½¹Ì°4(€€€€€€€€‰É½ÕÁ}½Õ¹ÑÌˆè½Õ¹ÑÌ°4(€€€€€€€€‰Í¡•‘Õ±•}ÕÍ•ˆè€‰Í¡•‘Õ±”ˆ¥¸…Ù…¥±…‰±”°4(€€€€€€€€‰Ý•…Ñ¡•É}ÕÍ•ˆè€‰Ý•…Ñ¡•Èˆ¥¸…Ù…¥±…‰±”°4(€€€ô4(4(4)‘•˜}ÉÕ±•}‰…Í•‘}™…±±‰…¬¡Ñ½‘…äè‘¥ÑmÍÑÈ°¹åt°€¨°…Ù…¥±…‰¥±¥Ñäè‘¥ÑmÍÑÈ°¹åt¤€´ø‘¥ÑmÍÑÈ°¹åtè4(€€€µ…Ñ¡•Ìè±¥ÍÑmÍÑÉt€ômt4(€€€Í¡½ÉÑ}Í±••À€ô€¡}Ñ½}™±½…Ð¡Ñ½‘…ä¹•Ð ‰Í±••Á}Í¡½ÉÑ}ÍÑÉ•…¬ˆ¤¤½È€À¤€øô€È4(€€€ÍÑÉ•ÍÌ€ô‰½½°¡Ñ½‘…ä¹•Ð ‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…œˆ¤¤½È‰½½°¡Ñ½‘…ä¹•Ð ‰¹½Ñ•Í}ÍÑÉ•ÍÍ}™±…}±…|Äˆ¤¤4(€€€±…Ñ•}½ÕÑ¥¹œ€ô‰½½°¡Ñ½‘…ä¹•Ð ‰±…Ñ•}½ÕÑ¥¹}™±…œˆ¤¤4(€€€‘É¥¹­¥¹œ€ô‰½½°¡Ñ½‘…ä¹•Ð ‰¹½Ñ•Í}¡…Í}‘É¥¹­¥¹œˆ¤¤4(€€€¡¥¡}™…Ð€ô‰½½°¡Ñ½‘…ä¹•Ð ‰¡¥¡}™…Ñ}™±…œˆ¤¤½È€ ¡}Ñ½}™±½…Ð¡Ñ½‘…ä¹•Ð ‰™…Ñ}ÙÍ|Ý‘}‘•±Ñ„ˆ¤¤½È€À¤€ø€ÄÈ¤4(€€€¡¥¡}­…°€ô€¡}Ñ½}™±½…Ð¡Ñ½‘…ä¹•Ð ‰­…±}ÙÍ|Ý‘}‘•±Ñ„ˆ¤¤½È€À¤€ø€ÌÔÀ4(€€€Í¡•‘Õ±•}‘•¹Í”€ô€¡}Ñ½}™±½…Ð¡Ñ½‘…ä¹•Ð ‰Í¡•‘Õ±•}Í…µ•}‘…å}•Ù•¹Ñ}½Õ¹Ðˆ¤¤½È€À¤€øô€Ì4(€€€ÍÁ•¹‘}ÍÁ¥­”€ô€¡}Ñ½}™±½…Ð¡Ñ½‘…ä¹•Ð ‰ÍÁ•¹‘¥¹}ÙÍ|Ý‘}‘•±Ñ„ˆ¤¤½È€À¤€ø€ÌÀÀÀ4(€€€Í½¥…±}±½…€ô‰½½°¡Ñ½‘…ä¹•Ð ‰¹½Ñ•Í}Í½¥…±}±½…‘}™±…œˆ¤¤4(€€€™…Ñ¥Õ”€ô‰½½°¡Ñ½‘…ä¹•Ð ‰¹½Ñ•Í}™…Ñ¥Õ•}™±…œˆ¤¤4(€€€™}ÍÁ•¹€ô€¡}Ñ½}™±½…Ð¡Ñ½‘…ä¹•Ð ‰•áÁ•¹Í•}™}½Õ¹Ðˆ¤¤½È€À¤€ø€À4(€€€Ý••­•¹‘}½É}±…Ñ”€ô‰½½°¡Ñ½‘…ä¹•Ð ‰¥Í}Ý••­•¹ˆ¤¤½È±…Ñ•}½ÕÑ¥¹œ4(4(€€€¥˜Í¡½ÉÑ}Í±••À…¹ÍÑÉ•ÍÌ…¹±…Ñ•}½ÕÑ¥¹œè4(€€€€€€€µ…Ñ¡•Ì¹…ÁÁ•¹ ‹ž~·žv‡žrƒ¦žÚh€¬ÍÑÉ•ÍÌÍ¥¹…°€¬±…Ñ”½ÕÑ¥¹œˆ¤4(€€€¥˜‘É¥¹­¥¹œ…¹€¡¡¥¡}™…Ð½È¡¥¡}­…°¤…¹Í¡•‘Õ±•}‘•¹Í”è4(€€€€€€€µ…Ñ¡•Ì¹…ÁÁ•¹ ‹¦ŽË¦H¿’òk¦Žž’ë–R€¬ƒ¦®c¢¢Î¨¿¦®cŽ
¯Ž·Ž«Žð€¬ƒ–öOš^—’ê#–ºk–¾–ê˜ˆ¤4(€€€¥˜ÍÁ•¹‘}ÍÁ¥­”…¹Í½¥…±}±½……¹™…Ñ¥Õ”è4(€€€€€€€µ…Ñ¡•Ì¹…ÁÁ•¹ ‹šR¿–ëŽ
çŽGŽ
“Ž
¼€¬Í½¥…°±½…€¬™…Ñ¥Õ”ˆ¤4(€€€¥˜™}ÍÁ•¹…¹Í¡½ÉÑ}Í±••À…¹Ý••­•¹‘}½É}±…Ñ”è4(€€€€€€€µ…Ñ¡•Ì¹…ÁÁ•¹ ‰šR¿–ëŽŽ
(€¬ƒž~·žv‡žr€€¬ƒ¦Çšr¬¿–’s¢†3–.WŽGŽ
ÿŽóŽÌˆ¤4(4(€€€µ…Ñ¡•€ô±•¸¡µ…Ñ¡•Ì¤€øô€Ä4(€€€É•ÑÕÉ¸ì4(€€€€€€€€‰É¥Í­}µ…Ñ¡•ˆèµ…Ñ¡•°4(€€€€€€€€‰‰±•¹‘•‘}Í½É”ˆè€À¸ØÔ¥˜µ…Ñ¡••±Í”€À¸ÌÈ°4(€€€€€€€€‰µ…Ñ¡•‘}™…Ñ½ÉÌˆèµ…Ñ¡•Ì°4(€€€€€€€€‰¹½}…±•ÉÑ}É•…Í½¸ˆè9½¹”¥˜µ…Ñ¡••±Í”€‰™…±±‰…­}¹½}µÕ±Ñ¥}™…Ñ½É}µ…Ñ ˆ°4(€€€€€€€€‰Õ¹…Ù…¥±…‰±•}É½ÕÁÌˆè…Ù…¥±…‰¥±¥Ñä¹•Ð ‰Õ¹…Ù…¥±…‰±•}É½ÕÁÌˆ°mt¤°4(€€€ô4(