from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence

from publish.read_daily_log import DailyLogSummary


def build_analysis_json(
    *,
    target_date: str,
    today_summary: DailyLogSummary,
    features_df: Any,
    exploratory_summary: Mapping[str, Any],
    regression_summary: Mapping[str, Any],
    tree_summary: Mapping[str, Any],
) -> dict[str, Any]:
    recent7 = features_df.sort_values("date").tail(7)
    late_count = int(recent7["late_outing_flag"].fillna(False).sum()) if len(recent7) and "late_outing_flag" in recent7 else 0
    fatigue_count = int(recent7["notes_fatigue_flag"].fillna(False).sum()) if len(recent7) else 0
    behavior = [f"直近7日で夜更かしが{late_count}回あった", f"疲労系Notesが{fatigue_count}日で見られた"]
    note_days = int((recent7["notes_present_flag"].fillna(False)).sum()) if len(recent7) and "notes_present_flag" in recent7 else 0
    recording = [f"Notes記録あり{note_days}/{len(recent7)}日", f"睡眠有効日{int(recent7['sleep_valid_flag'].fillna(False).sum())}/{len(recent7)}日"] if len(recent7) else []
    today_sleep_valid = bool(features_df.sort_values("date").tail(1)["sleep_valid_flag"].iloc[0]) if len(features_df) else False
    today_reason = features_df.sort_values("date").tail(1)["sleep_invalid_reason"].iloc[0] if len(features_df) else None
    risk_level = "low"
    matched_patterns = list(exploratory_summary.get("matched_today_conditions") or [])
    if len(matched_patterns) >= 2:
        risk_level = "high"
    elif len(matched_patterns) >= 1:
        risk_level = "medium"
    primary_focus = "集中維持"
    if risk_level == "high":
        primary_focus = "回復優先"
    elif risk_level == "medium":
        primary_focus = "負荷調整"
    sleep_hours = round((today_summary.sleep_duration_min or 0) / 60.0, 2) if today_sleep_valid and today_summary.sleep_duration_min is not None else None
    evidence_used: list[str] = []
    for item in list(exploratory_summary.get("evidence_used") or []):
        if isinstance(item, Mapping):
            source = item.get("source_type", "explore")
            feature = item.get("feature") or ",".join(item.get("features", [])) or item.get("message", "")
            evidence_used.append(f"{source}: {feature}")
        else:
            evidence_used.append(str(item))
    if behavior:
        evidence_used.append(f"recent_7d: {behavior[0]}")
    if sleep_hours is not None:
        evidence_used.append(f"sleep: 睡眠時間{sleep_hours}時間")
    elif not today_sleep_valid:
        evidence_used.append("sleep: 睡眠データ不明")
    if not matched_patterns:
        evidence_used.append("good_bad: 過去30日で明確な再現パターンは限定的")
    else:
        evidence_used.append(f"good_bad: 再現パターン{len(matched_patterns)}件")
    return {
        "target_date": target_date,
        "today_sleep_context": {
            "sleep_available": today_sleep_valid,
            "sleep_invalid_reason": today_reason if not today_sleep_valid else None,
            "sleep_hours": sleep_hours,
            "bedtime": (today_summary.sleep_start or "")[11:16] if today_summary.sleep_start else None,
            "sleep_score": today_summary.sleep_score,
        },
        "data_quality": {
            "sleep_valid_history_days": int(features_df["sleep_valid_flag"].fillna(False).sum()) if "sleep_valid_flag" in features_df else 0,
            "sleep_invalid_history_days": int((~features_df["sleep_valid_flag"].fillna(False)).sum()) if "sleep_valid_flag" in features_df else 0,
            "sleep_invalid_reason_counts": features_df["sleep_invalid_reason"].fillna("unknown").value_counts().to_dict() if "sleep_invalid_reason" in features_df else {},
        },
        "exploratory_summary": {
            "top_single_features_for_low_mood": list(exploratory_summary.get("top_single_features_for_low_mood") or []),
            "top_protective_features": list(exploratory_summary.get("top_protective_features") or []),
            "top_combination_patterns_for_low_mood": list(exploratory_summary.get("top_combination_patterns_for_low_mood") or []),
            "top_combination_patterns_for_high_mood": list(exploratory_summary.get("top_combination_patterns_for_high_mood") or []),
        },
        "recent_7d_summary": {"behavior_trend": behavior, "recording_trend": recording},
        "matched_today_conditions": matched_patterns,
        "matched_patterns_count": len(matched_patterns),
        "evidence_used": evidence_used,
        "regression_summary": dict(regression_summary),
        "tree_summary": dict(tree_summary),
        "risk_level": risk_level,
        "primary_focus": primary_focus,
        "reason_codes": list(exploratory_summary.get("reason_codes") or []),
        "meal_signal": "過去30日の食事差分は限定的",
        "notes_pattern_signal": "直近メモ傾向を優先参照",
        "location_pattern_signal": "場所パターンは補助情報として参照",
    }


def render_today_advice_from_analysis(
    *,
    analysis_json: Mapping[str, Any],
    model: str,
    chat_completion: Callable[..., str],
) -> str:
    if analysis_json.get("today_sleep_context", {}).get("sleep_available") is False and analysis_json.get("matched_patterns_count", 0) == 0 and not analysis_json.get("exploratory_summary", {}).get("top_single_features_for_low_mood"):
        return "昨夜の睡眠データは不明です。過去30日で明確な再現パターンは限定的なため、今日は記録を整えつつ負荷を上げすぎない進め方で様子を見てください。"
    prompt = (
        "分析済みJSONのみを使ってToday adviceを2〜4文の自然な日本語で作成。"
        "必ず1)今日の睡眠要約 2)過去30日のリスク 3)直近7日傾向1つ 4)具体行動1〜2個。"
        "Pythonが出していない因果を補わない。\n"
        f"analysis={json.dumps(analysis_json, ensure_ascii=False)}"
    )
    try:
        return chat_completion(
            model=model,
            system_prompt="あなたは朝メール用の短いToday adviceライター。出力は日本語本文のみ。",
            user_prompt=prompt,
        ).strip()
    except Exception:
        sleep = analysis_json.get("today_sleep_context", {})
        return (
            f"睡眠は{sleep.get('sleep_hours', '不明')}時間で、今日は{analysis_json.get('primary_focus', '負荷調整')}を意識する日です。"
            "過去30日の傾向では同条件で負荷が上がりやすいため、午前中の重い判断を絞り、昼に短く休んでください。"
        )
