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
        "sleep_heart_rate": _collect_numeric(today_summary, "sleep_heart_rate"),
        "deep_duration_min": _collect_numeric(today_summary, "deep_duration_min"),
        "rem_duration_min": _collect_numeric(today_summary, "rem_duration_min"),
    }
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

    return SleepInsightContext(today_values=today_values, trend_values=trend_values)


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
        "あなたは睡眠データから日本語の短い分析文を作るアシスタントです。\n"
        "出力はJSONのみで返してください。\n"
        "キーは sleep_analysis_jp と today_condition_forecast_jp の2つです。\n"
        "sleep_analysis_jp は2〜4文で、昨夜の睡眠の要約を書いてください。\n"
        "today_condition_forecast_jp は2〜4文で、今日の体調・集中力・疲労感・判断力の見通しを書いてください。\n"
        "医療断定や過剰な断定は避け、軽い行動提案は可です。\n"
        "未入力の項目は無理に使わず、推測で補わないでください。"
    )
    user_prompt = (
        f"target_date: {target_date}\n"
        f"today_values: {context.today_values}\n"
        f"trend_values: {context.trend_values}\n"
        "差分がある場合は today_condition_forecast_jp に反映してください。"
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
    result: dict[str, str] = {}
    if sleep_analysis:
        result["sleep_analysis_jp"] = sleep_analysis
    if today_forecast:
        result["today_condition_forecast_jp"] = today_forecast
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
