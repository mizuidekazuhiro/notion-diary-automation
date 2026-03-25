from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence

from publish.read_daily_log import DailyLogSummary


def build_analysis_json(
    *,
    target_date: str,
    today_summary: DailyLogSummary,
    features_df: Any,
    adopted_patterns: Sequence[Mapping[str, Any]],
    regression_summary: Mapping[str, Any],
) -> dict[str, Any]:
    recent7 = features_df.sort_values("date").tail(7)
    late_count = int(recent7["bedtime_after_0100_flag"].fillna(False).sum()) if len(recent7) else 0
    fatigue_count = int(recent7["notes_fatigue_flag"].fillna(False).sum()) if len(recent7) else 0
    behavior = [f"直近7日で夜更かしが{late_count}回あった", f"疲労系Notesが{fatigue_count}日で見られた"]
    note_days = int((recent7["notes_sentiment_label"].fillna("") != "").sum()) if len(recent7) else 0
    recording = [f"Notes記録あり{note_days}/{len(recent7)}日"]
    risk_level = "low"
    if len(adopted_patterns) >= 2:
        risk_level = "high"
    elif len(adopted_patterns) >= 1:
        risk_level = "medium"
    primary_focus = "集中維持"
    if risk_level == "high":
        primary_focus = "回復優先"
    elif risk_level == "medium":
        primary_focus = "負荷調整"
    return {
        "target_date": target_date,
        "today_sleep_context": {
            "sleep_hours": round((today_summary.sleep_duration_min or 0) / 60.0, 2) if today_summary.sleep_duration_min is not None else None,
            "bedtime": (today_summary.sleep_start or "")[11:16] if today_summary.sleep_start else None,
            "sleep_score": today_summary.sleep_score,
        },
        "recent_7d_summary": {"behavior_trend": behavior, "recording_trend": recording},
        "matched_patterns": list(adopted_patterns),
        "regression_summary": dict(regression_summary),
        "risk_level": risk_level,
        "primary_focus": primary_focus,
    }


def render_today_advice_from_analysis(
    *,
    analysis_json: Mapping[str, Any],
    model: str,
    chat_completion: Callable[..., str],
) -> str:
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
