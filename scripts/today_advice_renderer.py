from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from publish.read_daily_log import DailyLogSummary
from scripts.sleep_utils import resolve_sleep_duration_minutes

INTERNAL_NOTES_TERMS = ("Notesの記録品質", "品質が低い", "parse", "unknown_rate", "unknown")


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
    behavior = [f"直近7日で夜遅い外出が{late_count}回", f"疲労系Notesが{fatigue_count}日"]
    note_days = int((recent7["notes_present_flag"].fillna(False)).sum()) if len(recent7) and "notes_present_flag" in recent7 else 0
    recording = [f"Notes記録あり{note_days}/{len(recent7)}日", f"睡眠有効日{int(recent7['sleep_valid_flag'].fillna(False).sum())}/{len(recent7)}日"] if len(recent7) else []

    today_row = features_df.sort_values("date").tail(1).iloc[0] if len(features_df) else None
    resolved = resolve_sleep_duration_minutes(today_summary.sleep_start, today_summary.sleep_end, today_summary.sleep_duration_min)
    resolved_minutes = resolved.resolved_sleep_duration_min
    if (
        today_summary.resolved_sleep_duration_min is not None
        and today_summary.resolved_sleep_duration_min > 0
        and today_summary.sleep_duration_source == "derived_from_start_end"
    ):
        resolved_minutes = today_summary.resolved_sleep_duration_min
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
    trend_values = today_row if today_row is not None else None
    sleep_delta = None
    sleep_score_delta = None
    if trend_values is not None:
        raw_duration_delta = trend_values.get("sleep_duration_min_delta_vs_7d")
        raw_score_delta = trend_values.get("sleep_score_delta_vs_7d")
        sleep_delta = float(raw_duration_delta) / 60.0 if raw_duration_delta == raw_duration_delta and raw_duration_delta is not None else None
        sleep_score_delta = float(raw_score_delta) if raw_score_delta == raw_score_delta and raw_score_delta is not None else None
    sleep_valid_history_days = int(features_df["sleep_valid_flag"].fillna(False).sum()) if "sleep_valid_flag" in features_df else 0
    reason_codes = [str(code) for code in list(exploratory_summary.get("reason_codes") or [])]
    sleep_primary_reason = any("sleep" in code.lower() for code in reason_codes)
    sleep_should_mention = bool(
        today_sleep_valid
        and (
            (sleep_delta is not None and abs(sleep_delta) >= 0.75)
            or (sleep_score_delta is not None and abs(sleep_score_delta) >= 10)
            or sleep_primary_reason
            or primary_focus == "回復優先"
            or sleep_valid_history_days >= 5
        )
    )
    if sleep_should_mention:
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
            "sleep_vs_7d_delta_hours": sleep_delta,
            "sleep_score_vs_7d_delta": sleep_score_delta,
            "sleep_should_mention": sleep_should_mention,
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
        "reason_codes": reason_codes,
    }


def render_today_advice_from_analysis(*, analysis_json: Mapping[str, Any], model: str, chat_completion: Callable[..., str]) -> str:
    if analysis_json.get("skipped_reason"):
        return ""
    def _fallback_text() -> str:
        sleep = analysis_json.get("today_sleep_context", {})
        recent = analysis_json.get("recent_7d_summary", {}).get("behavior_trend", [])
        recent_text = "、".join(str(x) for x in recent[:2] if x) or "直近7日では行動パターンの偏りは小さめ"
        focus = analysis_json.get("primary_focus", "負荷調整")
        if sleep.get("sleep_available") and sleep.get("sleep_should_mention"):
            sleep_text = f"昨夜の睡眠は{sleep.get('sleep_hours', '不明')}時間で、起床直後の集中立ち上がりは{focus}寄りに設計するのが安全です。"
        else:
            sleep_text = f"今日は{focus}を軸に、午前の判断負荷を先に下げる前提で計画を組むのが安全です。"
        return (
            f"{sleep_text}{recent_text}という流れから、過去30日で再現性の高いパターンは限定的ですが、今日は午前中の重い判断を前半に詰め込まず、完了を先に1〜2件作ってから難度の高い案件へ入る順が適しています。"
            f"最初の一手は、いま着手中の案件を20〜30分で区切れる最小単位に分解し、午前の最初のブロックで1つ完了させることです。これにより午後の判断コストを下げつつ、{focus}の軸を実行面で維持できます。進捗の観測点も午前中に固定してください。"
        )

    if analysis_json.get("today_sleep_context", {}).get("sleep_available") is False and analysis_json.get("matched_patterns_count", 0) == 0:
        return _fallback_text()
    prompt = (
        "analysis JSONのみを根拠にToday adviceを日本語4〜6文で作成。"
        "文字数は260〜420字。"
        "3〜5文。最初の文は必ずしも睡眠から始めない。"
        "構成順は『強い根拠→直近7日または30日の傾向→今日の実務上の注意点→最初の一手』。"
        "睡眠は optional。today_sleep_context.sleep_should_mention が true のときだけ睡眠へ言及する。"
        "一般論は禁止。"
        "睡眠available時に『睡眠データ不明』は書かない。"
        "Notes の内部品質事情（品質が低い/parse/unknown等）は本文に書かない。"
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
        sentence_count = len([s for s in generated.replace("。", "。\n").splitlines() if s.strip()])
        if "analysis=" in generated:
            return generated
        if sleep.get("sleep_available") and "睡眠データ不明" in generated:
            return _fallback_text()
        if sentence_count > 10:
            return _fallback_text()
        if any(term in generated for term in INTERNAL_NOTES_TERMS):
            return _fallback_text()
        return generated
    except Exception:
        return _fallback_text()
