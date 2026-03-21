from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

import requests

from publish.read_daily_log import DailyLogSummary, read_daily_log

OPENAI_TIMEOUT = (5, 60)
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True)
class SleepInsightContext:
    today_values: dict[str, Any]
    trend_values: dict[str, Any]
    supporting_context: dict[str, Any]


def _safe_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _safe_float(value: object) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _collect_numeric(summary: DailyLogSummary, field_name: str) -> Optional[float]:
    return _safe_float(getattr(summary, field_name, None))


def build_sleep_insight_context(
    *,
    today_summary: DailyLogSummary,
    history_summaries: Sequence[DailyLogSummary],
) -> SleepInsightContext:
    # Fields sent to Sleep GPT as raw "today_values".
    today_values = {
        "sleep_start": _safe_text(today_summary.sleep_start),
        "sleep_end": _safe_text(today_summary.sleep_end),
        "sleep_duration_min": _collect_numeric(today_summary, "sleep_duration_min"),
        "sleep_score": _collect_numeric(today_summary, "sleep_score"),
        "sleep_source": _safe_text(today_summary.sleep_source),
        "readiness_stars": _collect_numeric(today_summary, "readiness_stars"),
        "readiness_hrv": _collect_numeric(today_summary, "readiness_hrv"),
        "readiness_bpm": _collect_numeric(today_summary, "readiness_bpm"),
        "baseline_hrv": _collect_numeric(today_summary, "baseline_hrv"),
        "baseline_waking_bpm": _collect_numeric(today_summary, "baseline_waking_bpm"),
        "sleep_heart_rate": _collect_numeric(today_summary, "sleep_heart_rate"),
        "deep_duration_min": _collect_numeric(today_summary, "deep_duration_min"),
        "rem_duration_min": _collect_numeric(today_summary, "rem_duration_min"),
    }
    # Only the following numeric fields receive a 7-day average in trend_values.
    trend_fields = [
        "sleep_duration_min",
        "sleep_score",
        "readiness_hrv",
        "readiness_bpm",
        "deep_duration_min",
        "rem_duration_min",
    ]
    trend_values: dict[str, Any] = {}
    for field_name in trend_fields:
        values = [
            value
            for value in (_collect_numeric(item, field_name) for item in history_summaries)
            if value is not None
        ]
        if not values:
            continue
        avg = sum(values) / len(values)
        trend_values[f"{field_name}_7d_avg"] = round(avg, 2)

    # Only the following numeric fields receive a today-vs-7d delta in trend_values.
    delta_fields = [
        "sleep_duration_min",
        "sleep_score",
        "readiness_hrv",
        "readiness_bpm",
    ]
    for field_name in delta_fields:
        today_value = today_values.get(field_name)
        avg_value = trend_values.get(f"{field_name}_7d_avg")
        if today_value is None or avg_value is None:
            continue
        trend_values[f"{field_name}_delta_vs_7d"] = round(today_value - avg_value, 2)

    yesterday = history_summaries[0] if history_summaries else None
    recent_3 = list(history_summaries[:3])
    def _trend(values: Sequence[Optional[float]]) -> Optional[str]:
        nums = [float(v) for v in reversed(values) if v is not None]
        if len(nums) < 3:
            return None
        if nums[0] < nums[1] < nums[2]:
            return "up"
        if nums[0] > nums[1] > nums[2]:
            return "down"
        return None

    supporting_context = {
        "mood": _safe_text(today_summary.mood),
        "notes": _safe_text(today_summary.notes),
        "diary": _safe_text(today_summary.diary),
        "location_summary": _safe_text(today_summary.location_summary),
        "activity_summary": _safe_text(today_summary.activity_summary),
        "meal_summary": _safe_text(today_summary.meal_summary),
        "done_count": today_summary.done_count,
        "drop_count": today_summary.drop_count,
        "done_tasks": list(today_summary.done_tasks),
        "drop_tasks": list(today_summary.drop_tasks),
        "vs_yesterday": {
            "sleep_duration_min_delta": round((today_values["sleep_duration_min"] - yesterday.sleep_duration_min), 2) if yesterday and today_values["sleep_duration_min"] is not None and yesterday.sleep_duration_min is not None else None,
            "sleep_score_delta": round((today_values["sleep_score"] - yesterday.sleep_score), 2) if yesterday and today_values["sleep_score"] is not None and yesterday.sleep_score is not None else None,
            "readiness_hrv_delta": round((today_values["readiness_hrv"] - yesterday.readiness_hrv), 2) if yesterday and today_values["readiness_hrv"] is not None and yesterday.readiness_hrv is not None else None,
            "readiness_bpm_delta": round((today_values["readiness_bpm"] - yesterday.readiness_bpm), 2) if yesterday and today_values["readiness_bpm"] is not None and yesterday.readiness_bpm is not None else None,
        },
        "recent_3day_trend": {
            "sleep_duration_min": _trend([item.sleep_duration_min for item in recent_3]),
            "sleep_score": _trend([item.sleep_score for item in recent_3]),
            "readiness_hrv": _trend([item.readiness_hrv for item in recent_3]),
            "readiness_bpm": _trend([item.readiness_bpm for item in recent_3]),
        },
    }

    return SleepInsightContext(
        today_values=today_values,
        trend_values=trend_values,
        supporting_context=supporting_context,
    )


def load_recent_daily_logs(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: str,
    days: int = 7,
) -> list[DailyLogSummary]:
    base = _iso_day(target_date)
    summaries: list[DailyLogSummary] = []
    for offset in range(1, days + 1):
        day = (base - timedelta(days=offset)).strftime("%Y-%m-%d")
        summary = read_daily_log(
            daily_log_read_url=daily_log_read_url,
            target_date=day,
            bearer_token=bearer_token,
        )
        if summary:
            summaries.append(summary)
    return summaries


def _build_prompts(target_date: str, context: SleepInsightContext) -> tuple[str, str]:
    system_prompt = (
        "あなたは睡眠データから日本語の分析文を作るアシスタントです。\n"
        "出力はJSONのみで返してください。\n"
        "キーは sleep_analysis_jp・today_condition_forecast_jp・today_advice の3つです。\n"
        "sleep_analysis_jp は2〜4文で、昨夜の睡眠データの分析を書いてください。\n"
        "today_condition_forecast_jp は2〜4文で、今日の体調・集中力・疲労感・判断力の見通しを書いてください。\n"
        "today_advice は400〜700字程度で、必ず `Today advice`、`直近の傾向`、`本日の状態`、`本日の進め方`、`総括` の順で構成してください。\n"
        "today_advice の各セクションは箇条書きではなく3〜5文程度の自然な連続文で書き、総括のみ最後に1文で締めてください。\n"
        "today_advice では一般論を避け、直近7日平均、前日比、直近平均との差分、直近3日連続の傾向、予定や未処理タスクなど使えるデータを優先して、事実→解釈→行動提案の順で自然につないでください。\n"
        "データから言えないことは断定せず、未入力の項目は無理に使わず、推測で補わないでください。\n"
        "『バランスの良い食事を心がけましょう』『適度に休憩しましょう』『無理せず過ごしましょう』のような抽象的な一般論は禁止です。\n"
        "sleep_analysis_jp は分析、today_condition_forecast_jp は予測、today_advice はその日の進め方の助言、という役割を厳密に分けてください。"
    )
    user_prompt = (
        f"target_date: {target_date}\n"
        f"today_values: {context.today_values}\n"
        f"trend_values: {context.trend_values}\n"
        f"supporting_context: {context.supporting_context}\n"
        "today_condition_forecast_jp には今日の見通しを反映し、today_advice にはメール冒頭にそのまま載せられる品質の長めの助言文を書いてください。"
    )
    return system_prompt, user_prompt


def generate_sleep_insights(
    *,
    target_date: str,
    context: SleepInsightContext,
) -> dict[str, str]:
    has_today_signal = any(
        value is not None and value != "" for value in context.today_values.values()
    )
    if not has_today_signal:
        return {}

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")

    system_prompt, user_prompt = _build_prompts(target_date, context)
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=OPENAI_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI response did not include sleep insights")

    import json

    parsed = json.loads(content)
    sleep_analysis = _safe_text(parsed.get("sleep_analysis_jp"))
    today_forecast = _safe_text(parsed.get("today_condition_forecast_jp"))
    today_advice = _safe_text(parsed.get("today_advice"))
    if not today_advice and (sleep_analysis or today_forecast):
        fragments = []
        duration = context.today_values.get("sleep_duration_min")
        if duration is not None:
            try:
                duration_minutes = int(round(float(duration)))
            except (TypeError, ValueError):
                duration_minutes = 0
            if duration_minutes and duration_minutes < 420:
                fragments.append("午前中は最重要の1件に絞り、昼に10〜15分だけ休憩を入れてください。")
        readiness_bpm = context.today_values.get("readiness_bpm")
        baseline_bpm = context.today_values.get("baseline_waking_bpm")
        if readiness_bpm is not None and baseline_bpm is not None and readiness_bpm - baseline_bpm >= 3:
            fragments.append("移動や会議の合間に深呼吸を入れ、午後前半は判断の重い作業を詰め込みすぎないでください。")
        if not fragments:
            fragments.append("午前中の早い時間に最優先の1件を終わらせ、午後はこまめに小休憩を入れてペースを整えてください。")
        today_advice = " ".join(fragments[:2])
    result: dict[str, str] = {}
    if sleep_analysis:
        result["sleep_analysis_jp"] = sleep_analysis
    if today_forecast:
        result["today_condition_forecast_jp"] = today_forecast
    if today_advice:
        result["today_advice"] = today_advice
    return result


def maybe_generate_sleep_insights(
    *,
    target_date: str,
    today_summary: DailyLogSummary,
    history_summaries: Sequence[DailyLogSummary],
) -> dict[str, str]:
    context = build_sleep_insight_context(
        today_summary=today_summary,
        history_summaries=history_summaries,
    )
    has_today_signal = any(
        value is not None and value != "" for value in context.today_values.values()
    )
    if not has_today_signal:
        logging.info(
            "Skipping sleep insight generation because no sleep signal is available. target_date=%s",
            target_date,
        )
        return {}
    try:
        result = generate_sleep_insights(target_date=target_date, context=context)
        if result:
            logging.info(
                "Generated sleep insights for overwrite save. target_date=%s keys=%s",
                target_date,
                sorted(result.keys()),
            )
        return result
    except Exception:
        logging.exception("Failed to generate sleep insights. target_date=%s", target_date)
        return {}
