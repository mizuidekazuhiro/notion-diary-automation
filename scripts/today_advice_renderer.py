from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Mapping

from publish.read_daily_log import DailyLogSummary
from scripts.daily_mail_quality import today_advice_char_limits
from scripts.advice_evidence import observed_targets

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
    today_sleep_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    window_start = (date.fromisoformat(target_date) - timedelta(days=6)).isoformat()
    recent7 = features_df[(features_df["date"] >= window_start) & (features_df["date"] <= target_date)].sort_values("date")
    late_count = int(recent7["late_outing_flag"].fillna(False).sum()) if len(recent7) and "late_outing_flag" in recent7 else 0
    fatigue_count = int(recent7["notes_fatigue_flag"].fillna(False).sum()) if len(recent7) else 0
    notes_quality = dict(notes_label_quality or {})
    notes_parse_success_rate = float(notes_quality.get("notes_parse_success_rate", 1.0) or 0.0)
    notes_label_quality_low = bool(notes_quality.get("label_quality_low")) or notes_parse_success_rate < 0.5
    location_days = int(recent7["late_outing_flag"].notna().sum()) if "late_outing_flag" in recent7 else 0
    fatigue_days = int(recent7["notes_fatigue_flag"].notna().sum()) if "notes_fatigue_flag" in recent7 else 0
    behavior = []
    if location_days:
        behavior.append(f"直近7日の位置記録がある{location_days}日中、夜遅い外出の記載が{late_count}日")
    if fatigue_days:
        behavior.append(f"解析できたメモ{fatigue_days}日中、疲労の記載が{fatigue_count}日")
    note_days = int((recent7["notes_present_flag"].fillna(False)).sum()) if len(recent7) and "notes_present_flag" in recent7 else 0
    recording = [f"Notes記録あり{note_days}/{len(recent7)}日", f"睡眠有効日{int(recent7['sleep_valid_flag'].fillna(False).sum())}/{len(recent7)}日"] if len(recent7) else []

    target_rows = features_df[features_df["date"] == target_date] if "date" in features_df else []
    today_row = target_rows.iloc[0] if len(target_rows) else None
    sleep_context = dict(today_sleep_context or {})
    today_sleep_valid = bool(sleep_context.get("sleep_available"))
    sleep_hours = sleep_context.get("sleep_hours")
    sleep_score = sleep_context.get("sleep_score")
    today_reason = sleep_context.get("sleep_invalid_reason")

    matched_patterns = list(exploratory_summary.get("matched_today_conditions") or [])
    _, target_counts, target_reason = observed_targets(features_df)
    # These are exploratory same-day associations, without prospective validation
    # or personal calibration. Neither match counts nor fitted scores establish risk.
    risk_level = "unknown"
    primary_focus = "記録に基づく確認"

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

    return {
        "target_date": target_date,
        "reflection_date": target_date,
        "advice_target_date": (date.fromisoformat(target_date) + timedelta(days=1)).isoformat(),
        "target_counts": target_counts,
        "assessment_status": "insufficient_data" if target_reason else "exploratory_only",
        "calibrated_for_personal_prediction": False,
        "today_sleep_context": {
            **sleep_context,
            "sleep_available": today_sleep_valid,
            "sleep_invalid_reason": today_reason if not today_sleep_valid else None,
            "sleep_hours": sleep_hours if today_sleep_valid else None,
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


ADVICE_PROMPT_VERSION = "evidence-only-v2"


@dataclass(frozen=True)
class AdviceRenderResult:
    text: str
    status: str
    method: str
    fallback_used: bool
    rejection_reason: str | None
    model: str
    prompt_version: str
    input_hash: str


def render_today_advice_result(*, analysis_json: Mapping[str, Any], model: str, chat_completion: Callable[..., str]) -> AdviceRenderResult:
    digest = hashlib.sha256(json.dumps(analysis_json, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    def result(text: str, status: str, method: str, fallback: bool = False, reason: str | None = None) -> AdviceRenderResult:
        return AdviceRenderResult(text, status, method, fallback, reason, model, ADVICE_PROMPT_VERSION, digest)

    if analysis_json.get("skipped_reason"):
        return result("記録の分析を完了できなかったため、今日への助言は控えます。", "degraded", "deterministic", True, str(analysis_json["skipped_reason"]))
    counts = analysis_json.get("target_counts", {})
    if counts.get("valid") == 0 or analysis_json.get("assessment_status") == "insufficient_data":
        return result("気分と行動を比較できる記録が足りないため、今日のリスクは判定できません。記録から裏付けられる新たな助言はありません。", "insufficient_data", "deterministic")
    if not analysis_json.get("evidence_used") and not analysis_json.get("today_sleep_context", {}).get("sleep_available"):
        return result("今日に結び付けられる十分な記録がないため、具体的な助言は控えます。", "insufficient_data", "deterministic")
    _, max_chars = today_advice_char_limits()
    prompt = (
        "与えた分析資料だけを根拠に、翌日への助言を日本語で簡潔に書いてください。"
        f"上限{max_chars}字。最低文字数はありません。根拠が少なければ1文で構いません。"
        "reflection_dateは振り返る日、advice_target_dateは助言の対象日です。"
        "過去の探索的関連は因果や予測精度ではありません。数値確率や高・低リスクを断定しないでください。"
        "未知や未記録を0回・問題なしと解釈しないでください。"
        "当日の根拠がない午前の負荷調整、回復優先、作業時間の指定を追加しないでください。"
        "睡眠に触れるのはsleep_should_mentionがtrueのときだけです。"
        "内部変数、英語の項目名、解析品質の技術用語を本文に出さないでください。"
        "裏付けのある提案がなければ、その旨だけを短く書いてください。\n"
        f"分析資料={json.dumps(analysis_json, ensure_ascii=False)}"
    )
    try:
        generated = chat_completion(
            model=model,
            system_prompt="あなたは朝メール用のToday adviceライター。出力は日本語本文のみ。",
            user_prompt=prompt,
        ).strip()
        sleep = analysis_json.get("today_sleep_context", {})
        reason = None
        if not generated:
            reason = "empty_generation"
        elif len("".join(generated.split())) > max_chars:
            reason = "output_too_long"
        elif any(term in generated for term in (*INTERNAL_NOTES_TERMS, "analysis=", "risk_level", "sleep_", "target_counts", "分析資料=")):
            reason = "internal_terms"
        elif sleep.get("sleep_available") and "睡眠データ不明" in generated:
            reason = "contradictory_sleep_claim"
        if reason:
            return result("記録に忠実な助言を作成できなかったため、今回は助言を控えます。", "degraded", "deterministic", True, reason)
        return result(generated, "success", "ai")
    except Exception as exc:
        return result("今日への助言を作成できませんでした。日記の記録を確認してください。", "degraded", "deterministic", True, type(exc).__name__)


def render_today_advice_from_analysis(*, analysis_json: Mapping[str, Any], model: str, chat_completion: Callable[..., str]) -> str:
    """Text-only compatibility entrypoint; the production pipeline uses the result."""
    return render_today_advice_result(analysis_json=analysis_json, model=model, chat_completion=chat_completion).text
