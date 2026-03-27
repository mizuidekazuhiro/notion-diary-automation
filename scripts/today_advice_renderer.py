from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from publish.read_daily_log import DailyLogSummary
from scripts.sleep_utils import resolve_sleep_duration_minutes


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
    notes_quality = dict(notes_label_quality or {})
    notes_parse_success_rate = float(notes_quality.get("notes_parse_success_rate", 1.0) or 0.0)
    notes_label_quality_low = bool(notes_quality.get("label_quality_low")) or notes_parse_success_rate < 0.5
    if notes_label_quality_low:
        behavior = [f"直近7日で夜遅い外出が{late_count}回", "Notesから明確な傾向は十分抽出できませんでした"]
    else:
        behavior = [f"直近7日で夜遅い外出が{late_count}回", f"疲労系Notesが{fatigue_count}日"]
    note_days = int((recent7["notes_present_flag"].fillna(False)).sum()) if len(recent7) and "notes_present_flag" in recent7 else 0
    recording = [f"Notes記録あり{note_days}/{len(recent7)}日", f"睡眠有効日{int(recent7['sleep_valid_flag'].fillna(False).sum())}/{len(recent7)}日"] if len(recent7) else []

    today_row = features_df.sort_values("date").tail(1).iloc[0] if len(features_df) else None
    resolved = resolve_sleep_duration_minutes(today_summary.sleep_start, today_summary.sleep_end, today_summary.sleep_duration_min)
    resolved_minutes = resolved.resolved_sleep_duration_min
    summary_sleep_score = float(today_summary.sleep_score) if isinstance(today_summary.sleep_score, (int, float)) else None
    today_sleep_valid = bool(
        (resolved_minutes is not None and resolved_minutes > 0)
        or (summary_sleep_score is not None and summary_sleep_score > 0)
    )
    today_reason = None if today_sleep_valid else (resolved.invalid_reason or (today_row.get("sleep_invalid_reason") if today_row is not None else "missing_sleep_signal"))
    sleep_hours = round(resolved_minutes / 60.0, 2) if today_sleep_valid and resolved_minutes is not None else None
    sleep_score = summary_sleep_score if today_sleep_valid and summary_sleep_score is not None else (
        float(today_row.get("sleep_score")) if today_row is not None and today_sleep_valid and today_row.get("sleep_score") == today_row.get("sleep_score") else None
    )

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
        if notes_label_quality_low:
            evidence_used.append("notes: 構造化品質が低く、Notes由来の断定を抑制")
    evidence_used.append("sleep: 睡眠データ不明" if not today_sleep_valid else f"sleep: 睡眠時間{sleep_hours}時間・スコア{sleep_score if sleep_score is not None else '不明'}")
    if not matched_patterns:
        evidence_used.append("good_bad: 過去30日で明確な再現パターンは限定的")

    return {
        "target_date": target_date,
        "today_sleep_context": {
            "sleep_available": today_sleep_valid,
            "sleep_invalid_reason": today_reason if not today_sleep_valid else None,
            "sleep_hours": sleep_hours if today_sleep_valid else None,
            "duration_source": resolved.duration_source,
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
        return "睡眠データが不明です。過去30〜60日の探索では再現性の高い単独パターンは限定的でした。今日は支出やタスクの重い判断を午前後半に寄せ、先に完了を2件作る進め方が安全です。Notes由来の低信頼シグナルは断定せず、進捗観測を優先してください。"
    prompt = (
        "analysis JSONのみを根拠にToday adviceを日本語3〜5文で作成。"
        "睡眠invalidなら『睡眠データ不明』と書く。"
        "一般論を避け、根拠が弱い場合は弱いと明記。"
        "analysis JSON以外の因果は追加禁止。\n"
        f"analysis={json.dumps(analysis_json, ensure_ascii=False)}"
    )
    try:
        generated = chat_completion(
            model=model,
            system_prompt="あなたは朝メール用のToday adviceライター。出力は日本語本文のみ。",
            user_prompt=prompt,
        ).strip()
        sleep = analysis_json.get("today_sleep_context", {})
        if sleep.get("sleep_available") and "睡眠" not in generated:
            hours = sleep.get("sleep_hours")
            prefix = f"昨夜の睡眠は{hours}時間で、今日は負荷調整を意識してください。" if hours is not None else "昨夜の睡眠データを踏まえ、今日は負荷調整を意識してください。"
            return f"{prefix}{generated}"
        return generated
    except Exception:
        sleep = analysis_json.get("today_sleep_context", {})
        if sleep.get("sleep_available") is False:
            return "睡眠データが不明です。過去30〜60日の探索では同条件で支出増・完了率低下が重なる日に翌日ムードが落ちやすい傾向があります。今日は新規着手より進行中タスク完了を2件先に作り、支出判断は午後に回してください。"
        return (
            f"睡眠は{sleep.get('sleep_hours', '不明')}時間で、今日は{analysis_json.get('primary_focus', '負荷調整')}を意識する日です。"
            "過去30日の傾向では同条件で負荷が上がりやすいため、午前中の重い判断を絞ってください。"
        )
