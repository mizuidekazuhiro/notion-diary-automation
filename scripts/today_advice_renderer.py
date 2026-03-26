from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from publish.read_daily_log import DailyLogSummary


def build_analysis_json(
    *,
    target_date: str,
    today_summary: DailyLogSummary,
    features_df: Any,
    exploratory_summary: Mapping[str, Any],
    regression_summary: Mapping[str, Any],
    lightgbm_summary: Mapping[str, Any],
    notes_label_quality: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    recent7 = features_df.sort_values("date").tail(7)
    late_count = int(recent7["late_outing_flag"].fillna(False).sum()) if len(recent7) and "late_outing_flag" in recent7 else 0
    fatigue_count = int(recent7["notes_fatigue_flag"].fillna(False).sum()) if len(recent7) else 0
    behavior = [f"直近7日で夜遅い外出が{late_count}回", f"疲労系Notesが{fatigue_count}日"]
    note_days = int((recent7["notes_present_flag"].fillna(False)).sum()) if len(recent7) and "notes_present_flag" in recent7 else 0
    recording = [f"Notes記録あり{note_days}/{len(recent7)}日", f"睡眠有効日{int(recent7['sleep_valid_flag'].fillna(False).sum())}/{len(recent7)}日"] if len(recent7) else []

    today_row = features_df.sort_values("date").tail(1).iloc[0] if len(features_df) else None
    today_sleep_valid = bool(today_row.get("sleep_valid_flag", False)) if today_row is not None else False
    today_reason = today_row.get("sleep_invalid_reason") if today_row is not None else None
    sleep_hours = round(float(today_row.get("sleep_hours")), 2) if today_row is not None and today_sleep_valid and today_row.get("sleep_hours") == today_row.get("sleep_hours") else None
    sleep_score = float(today_row.get("sleep_score")) if today_row is not None and today_sleep_valid and today_row.get("sleep_score") == today_row.get("sleep_score") else None

    matched_patterns = list(exploratory_summary.get("matched_today_conditions") or [])
    prob = lightgbm_summary.get("prediction_probability_for_today")
    risk_level = "low"
    if isinstance(prob, (int, float)):
        risk_level = "high" if prob >= 0.6 else "medium" if prob >= 0.35 else "low"
    elif len(matched_patterns) >= 2:
        risk_level = "high"
    elif len(matched_patterns) >= 1:
        risk_level = "medium"

    primary_focus = "集中維持"
    if risk_level == "high":
        primary_focus = "回復優先"
    elif risk_level == "medium":
        primary_focus = "負荷調整"

    evidence_used: list[str] = []
    for item in list(exploratory_summary.get("evidence_used") or []):
        if isinstance(item, Mapping):
            source = item.get("source_type", "explore")
            feature = item.get("feature") or ",".join(item.get("features", [])) or item.get("message", "")
            evidence_used.append(f"{source}: {feature}")
    if behavior:
        evidence_used.append(f"recent_7d: {behavior[0]}")
    evidence_used.append("sleep: 睡眠データ不明" if not today_sleep_valid else f"sleep: 睡眠時間{sleep_hours}時間")
    if not matched_patterns:
        evidence_used.append("good_bad: 過去30日で明確な再現パターンは限定的")

    return {
        "target_date": target_date,
        "today_sleep_context": {
            "sleep_available": today_sleep_valid,
            "sleep_invalid_reason": today_reason if not today_sleep_valid else None,
            "sleep_hours": sleep_hours if today_sleep_valid else None,
            "bedtime": (today_summary.sleep_start or "")[11:16] if today_sleep_valid and today_summary.sleep_start else None,
            "sleep_score": sleep_score if today_sleep_valid else None,
        },
        "data_quality": {
            "sleep_valid_history_days": int(features_df["sleep_valid_flag"].fillna(False).sum()) if "sleep_valid_flag" in features_df else 0,
            "sleep_invalid_history_days": int((~features_df["sleep_valid_flag"].fillna(False)).sum()) if "sleep_valid_flag" in features_df else 0,
            "sleep_invalid_reason_counts": features_df["sleep_invalid_reason"].fillna("unknown").value_counts().to_dict() if "sleep_invalid_reason" in features_df else {},
            "notes_label_quality": dict(notes_label_quality or {}),
        },
        "exploratory_summary": {
            "top_single_features_for_low_mood": list(exploratory_summary.get("top_single_features_for_low_mood") or []),
            "top_protective_features": list(exploratory_summary.get("top_protective_features") or []),
            "top_combination_patterns_for_low_mood": list(exploratory_summary.get("top_combination_patterns_for_low_mood") or []),
            "top_combination_patterns_for_high_mood": list(exploratory_summary.get("top_combination_patterns_for_high_mood") or []),
        },
        "regression_summary": dict(regression_summary),
        "lightgbm_summary": dict(lightgbm_summary),
        "recent_7d_summary": {"behavior_trend": behavior, "recording_trend": recording},
        "matched_today_conditions": matched_patterns,
        "matched_patterns_count": len(matched_patterns),
        "evidence_used": evidence_used,
        "risk_level": risk_level,
        "primary_focus": primary_focus,
        "reason_codes": list(exploratory_summary.get("reason_codes") or []),
    }


def render_today_advice_from_analysis(*, analysis_json: Mapping[str, Any], model: str, chat_completion: Callable[..., str]) -> str:
    if analysis_json.get("today_sleep_context", {}).get("sleep_available") is False and analysis_json.get("matched_patterns_count", 0) == 0:
        return "昨夜の睡眠データは不明です。過去30日で明確な再現パターンは限定的なため、今日は負荷を上げすぎない進め方で様子を見てください。"
    prompt = (
        "analysis JSONのみを根拠にToday adviceを日本語2〜4文で作成。"
        "睡眠invalidなら『睡眠データ不明』と書く。"
        "一般論を避け、根拠が弱い場合は弱いと明記。"
        "analysis JSON以外の因果は追加禁止。\n"
        f"analysis={json.dumps(analysis_json, ensure_ascii=False)}"
    )
    try:
        return chat_completion(
            model=model,
            system_prompt="あなたは朝メール用のToday adviceライター。出力は日本語本文のみ。",
            user_prompt=prompt,
        ).strip()
    except Exception:
        sleep = analysis_json.get("today_sleep_context", {})
        if sleep.get("sleep_available") is False:
            return "昨夜の睡眠データは不明です。過去傾向から今日は負荷を段階的に上げ、午前は最重要1件に絞ってください。"
        return (
            f"睡眠は{sleep.get('sleep_hours', '不明')}時間で、今日は{analysis_json.get('primary_focus', '負荷調整')}を意識する日です。"
            "過去30日の傾向では同条件で負荷が上がりやすいため、午前中の重い判断を絞ってください。"
        )
