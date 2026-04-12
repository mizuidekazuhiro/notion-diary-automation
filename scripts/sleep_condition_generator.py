from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

import requests

from publish.read_daily_log import DailyLogSummary, read_daily_log
from scripts.sleep_utils import (
    resolve_sleep_duration_minutes,
    resolve_canonical_sleep_metrics,
    validate_generated_sleep_text,
)

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
    resolved_duration_min = _safe_float(getattr(today_summary, "resolved_sleep_duration_min", None))
    resolved_duration_hours = _safe_float(getattr(today_summary, "resolved_sleep_duration_hours", None))
    resolved_duration_text = _safe_text(getattr(today_summary, "resolved_sleep_duration_text", None))

    if resolved_duration_min is not None:
        canonical_today = resolve_canonical_sleep_metrics(
            today_summary.sleep_start,
            today_summary.sleep_end,
            resolved_duration_min,
        )
        canonical_today = dataclasses.replace(
            canonical_today,
            resolved_sleep_duration_hours=resolved_duration_hours if resolved_duration_hours is not None else canonical_today.resolved_sleep_duration_hours,
            resolved_sleep_duration_text=resolved_duration_text or canonical_today.resolved_sleep_duration_text,
            sleep_duration_source=_safe_text(getattr(today_summary, "sleep_duration_source", None)) or canonical_today.sleep_duration_source,
        )
    else:
        canonical_today = resolve_canonical_sleep_metrics(
            today_summary.sleep_start,
            today_summary.sleep_end,
            today_summary.sleep_duration_min,
        )

    today_duration = canonical_today.resolved_sleep_duration_min
    today_values = {
        "sleep_start": _safe_text(today_summary.sleep_start),
        "sleep_end": _safe_text(today_summary.sleep_end),
        "sleep_duration_min": today_duration,
        "canonical_sleep_duration_min": canonical_today.resolved_sleep_duration_min,
        "canonical_sleep_duration_hours": canonical_today.resolved_sleep_duration_hours,
        "canonical_sleep_duration_text": canonical_today.resolved_sleep_duration_text,
        "sleep_duration_source": canonical_today.sleep_duration_source,
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
        values = []
        for item in history_summaries:
            if field_name == "sleep_duration_min":
                value = resolve_sleep_duration_minutes(item.sleep_start, item.sleep_end, item.sleep_duration_min).resolved_sleep_duration_min
            else:
                value = _collect_numeric(item, field_name)
            if value is not None:
                values.append(value)
        avg = round(sum(values) / len(values), 2) if values else None
        trend_values[f"{field_name}_7d_avg"] = avg

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
        trend_values[f"{field_name}_delta_vs_7d"] = (
            round(today_value - avg_value, 2)
            if today_value is not None and avg_value is not None
            else None
        )

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

    recent_3day_trend = {
        "sleep_duration_min": _trend([resolve_sleep_duration_minutes(item.sleep_start, item.sleep_end, item.sleep_duration_min).resolved_sleep_duration_min for item in recent_3]),
        "sleep_score": _trend([item.sleep_score for item in recent_3]),
        "readiness_hrv": _trend([item.readiness_hrv for item in recent_3]),
        "readiness_bpm": _trend([item.readiness_bpm for item in recent_3]),
    }
    yesterday_duration = (
        resolve_sleep_duration_minutes(yesterday.sleep_start, yesterday.sleep_end, yesterday.sleep_duration_min).resolved_sleep_duration_min
        if yesterday
        else None
    )
    vs_yesterday = {
        "sleep_duration_min_delta": round((today_values["sleep_duration_min"] - yesterday_duration), 2) if yesterday and today_values["sleep_duration_min"] is not None and yesterday_duration is not None else None,
        "sleep_score_delta": round((today_values["sleep_score"] - yesterday.sleep_score), 2) if yesterday and today_values["sleep_score"] is not None and yesterday.sleep_score is not None else None,
        "readiness_hrv_delta": round((today_values["readiness_hrv"] - yesterday.readiness_hrv), 2) if yesterday and today_values["readiness_hrv"] is not None and yesterday.readiness_hrv is not None else None,
        "readiness_bpm_delta": round((today_values["readiness_bpm"] - yesterday.readiness_bpm), 2) if yesterday and today_values["readiness_bpm"] is not None and yesterday.readiness_bpm is not None else None,
    }
    trend_values["vs_yesterday"] = vs_yesterday
    trend_values["recent_3day_trend"] = recent_3day_trend

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
        "vs_yesterday": vs_yesterday,
        "recent_3day_trend": recent_3day_trend,
    }

    return SleepInsightContext(
        today_values=today_values,
        trend_values=trend_values,
        supporting_context=supporting_context,
    )




def _is_invalid_sleep_record(summary: DailyLogSummary) -> tuple[bool, str | None]:
    resolved = resolve_sleep_duration_minutes(summary.sleep_start, summary.sleep_end, summary.sleep_duration_min)
    duration = resolved.resolved_sleep_duration_min
    score = _safe_float(summary.sleep_score)
    if duration is None:
        if resolved.invalid_reason == "duration_non_positive" and score == 0:
            return True, "duration_and_score_zero"
        return True, resolved.invalid_reason or "missing_duration"
    return False, None

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




def _dump_sleep_insights_debug_log(
    *,
    debug_kind: str,
    target_date: str,
    model: str,
    full_input: Mapping[str, Any],
    input_summary: Mapping[str, Any],
    prompt_text: str,
) -> None:
    try:
        full_input_json = json.dumps(full_input, ensure_ascii=False, indent=2, default=str)
        advice_summary_json = json.dumps(input_summary, ensure_ascii=False, indent=2, default=str)
        print(f"=== SLEEP INSIGHTS {debug_kind} FULL INPUT START ===")
        print(full_input_json)
        print(f"=== SLEEP INSIGHTS {debug_kind} FULL INPUT END ===")
        print()
        print(f"=== SLEEP INSIGHTS {debug_kind} INPUT SUMMARY START ===")
        print(advice_summary_json)
        print(f"=== SLEEP INSIGHTS {debug_kind} INPUT SUMMARY END ===")
        print()
        print(f"=== SLEEP INSIGHTS {debug_kind} MODEL START ===")
        print(model)
        print(f"=== SLEEP INSIGHTS {debug_kind} MODEL END ===")
        print()
        print(f"=== SLEEP INSIGHTS {debug_kind} TARGET DATE START ===")
        print(target_date)
        print(f"=== SLEEP INSIGHTS {debug_kind} TARGET DATE END ===")
        print()
        print(f"=== SLEEP INSIGHTS {debug_kind} PROMPT START ===")
        print(prompt_text)
        print(f"=== SLEEP INSIGHTS {debug_kind} PROMPT END ===")
    except Exception as exc:
        logging.warning("sleep_insights_debug_print_failed kind=%s error=%s", debug_kind, exc)

    try:
        debug_dir = os.path.join(os.getcwd(), "debug")
        os.makedirs(debug_dir, exist_ok=True)
        debug_payload = {
            "target_date": target_date,
            "model": model,
            "full_input": full_input,
            "input_summary": input_summary,
            "prompt_text": prompt_text,
        }
        debug_path = os.path.join(debug_dir, f"sleep_insights_{debug_kind.lower()}_full_{target_date}.json")
        with open(debug_path, "w", encoding="utf-8") as debug_file:
            json.dump(debug_payload, debug_file, ensure_ascii=False, indent=2, default=str)
        print(f"=== SLEEP INSIGHTS {debug_kind} DEBUG FILE START ===")
        print(debug_path)
        print(f"=== SLEEP INSIGHTS {debug_kind} DEBUG FILE END ===")
        summary_path = os.path.join(debug_dir, f"sleep_insights_{debug_kind.lower()}_summary_{target_date}.json")
        with open(summary_path, "w", encoding="utf-8") as summary_file:
            json.dump(input_summary, summary_file, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        logging.warning("sleep_insights_debug_file_failed kind=%s error=%s", debug_kind, exc)


def _build_sleep_advice_debug_summary(*, context: SleepInsightContext) -> dict[str, Any]:
    return {
        "today_values_keys": sorted(context.today_values.keys()),
        "today_values_present_count": sum(1 for value in context.today_values.values() if value not in (None, "")),
        "trend_values_keys": sorted(context.trend_values.keys()),
        "trend_values_present_count": sum(1 for value in context.trend_values.values() if value not in (None, "")),
        "minimal_sleep_signal": sum(1 for value in context.today_values.values() if value not in (None, "")) <= 2,
        "supporting_context_keys": sorted(context.supporting_context.keys()),
        "done_tasks_count": len(context.supporting_context.get("done_tasks", [])),
        "drop_tasks_count": len(context.supporting_context.get("drop_tasks", [])),
        "has_notes": bool(context.supporting_context.get("notes")),
        "has_diary": bool(context.supporting_context.get("diary")),
        "has_location_summary": bool(context.supporting_context.get("location_summary")),
        "has_activity_summary": bool(context.supporting_context.get("activity_summary")),
        "has_meal_summary": bool(context.supporting_context.get("meal_summary")),
    }

def _build_prompts(target_date: str, context: SleepInsightContext) -> tuple[str, str]:
    system_prompt = (
        "あなたは睡眠データから日本語の分析文を作るアシスタントです。\n"
        "出力はJSONのみで返してください。\n"
        "キーは sleep_analysis_jp・today_condition_forecast_jp の2つです。\n"
        "sleep_analysis_jp は2〜4文で、昨夜の睡眠データの分析を書いてください。\n"
        "today_condition_forecast_jp は2〜4文で、今日の体調・集中力・疲労感・判断力の見通しを書いてください。\n"
        "canonical sleep duration is authoritative（正規睡眠時間が唯一の真実）です。\n"
        "時刻文字列から睡眠時間を再計算してはいけません。\n"
        "睡眠時間は supplied canonical value をそのまま使ってください。\n"
        "睡眠時間に言及する場合、canonical_sleep_duration_text と完全一致させてください。\n"
        "データから言えないことは断定せず、未入力の項目は無理に使わず、推測で補わないでください。\n"
        "『バランスの良い食事を心がけましょう』『適度に休憩しましょう』『無理せず過ごしましょう』のような抽象的な一般論は禁止です。\n"
        "sleep_analysis_jp は分析、today_condition_forecast_jp は予測として扱い、助言文やメール冒頭向け本文は絶対に生成しないでください。"
    )
    user_prompt = (
        f"target_date: {target_date}\n"
        f"today_values: {context.today_values}\n"
        f"trend_values: {context.trend_values}\n"
        f"supporting_context: {context.supporting_context}\n"
        "注意: canonical_sleep_duration_min/canonical_sleep_duration_text が提供されている場合は必ずそれを優先し、sleep_start/sleep_end から再計算しないこと。\n"
        "today_condition_forecast_jp には今日の見通しを反映してください。today_advice のような助言文は出力しないでください。"
    )
    return system_prompt, user_prompt


def _deterministic_sleep_fallback(context: SleepInsightContext) -> dict[str, str]:
    today = context.today_values
    duration_text = _safe_text(today.get("canonical_sleep_duration_text"))
    sleep_score = _safe_float(today.get("sleep_score"))
    score_text = f"睡眠スコアは{int(sleep_score)}" if sleep_score is not None else "睡眠スコアは未計測"
    if duration_text:
        analysis = f"昨夜の睡眠時間は{duration_text}です。{score_text}で、睡眠記録はcanonical値に基づいています。"
        forecast = f"今日のコンディションは、昨夜の睡眠{duration_text}と{score_text}を前提に、午前の立ち上がりを観察しながら調整する見通しです。"
    else:
        analysis = "昨夜の睡眠時間は確定値を取得できませんでした。睡眠関連の指標は欠損を含むため、評価は保守的に扱います。"
        forecast = "今日のコンディション見通しは、睡眠時間の確定値がないため、午前中の負荷を抑えて観察する前提が適切です。"
    return {"sleep_analysis_jp": analysis, "today_condition_forecast_jp": forecast}


def _validate_sleep_outputs(result: dict[str, str], context: SleepInsightContext) -> tuple[bool, str | None]:
    canonical_min = context.today_values.get("canonical_sleep_duration_min")
    canonical_text = context.today_values.get("canonical_sleep_duration_text")
    for key in ("sleep_analysis_jp", "today_condition_forecast_jp"):
        value = result.get(key)
        validation = validate_generated_sleep_text(
            value,
            canonical_sleep_duration_min=canonical_min,
            canonical_sleep_duration_text=canonical_text,
        )
        if not validation.is_consistent:
            logging.warning(
                "sleep_text_consistency_error field=%s expected_sleep_duration_text=%s found_duration_text=%s",
                key,
                canonical_text,
                validation.found_duration_text,
            )
            return False, validation.found_duration_text
    return True, None


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
    full_input = {
        "today_values": context.today_values,
        "trend_values": context.trend_values,
        "supporting_context": context.supporting_context,
    }
    prompt_text = f"[system]\n{system_prompt}\n\n[user]\n{user_prompt}"
    _dump_sleep_insights_debug_log(
        debug_kind="SLEEP",
        target_date=target_date,
        model=model,
        full_input=full_input,
        input_summary=_build_sleep_advice_debug_summary(context=context),
        prompt_text=prompt_text,
    )
    max_attempts = 2
    last_validation_error: str | None = None
    for attempt in range(max_attempts):
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
        is_valid, found_text = _validate_sleep_outputs(result, context)
        if is_valid:
            return result
        last_validation_error = found_text
        if attempt < max_attempts - 1:
            logging.warning("sleep_text_consistency_retry attempt=%s", attempt + 1)
            continue
    logging.warning("sleep_text_consistency_fallback reason=validation_failed found_duration_text=%s", last_validation_error)
    return _deterministic_sleep_fallback(context)


def maybe_generate_sleep_insights(
    *,
    target_date: str,
    today_summary: DailyLogSummary,
    history_summaries: Sequence[DailyLogSummary],
) -> dict[str, str]:
    invalid, reason = _is_invalid_sleep_record(today_summary)
    if invalid:
        logging.info("phase_c_sleep_invalid target_date=%s sleep_invalid_reason=%s sleep_text_mode=missing", target_date, reason)
        return {
            "sleep_analysis_jp": "昨夜の睡眠データは取得できていません。Apple Watch 未装着などにより記録が欠損している可能性があります。",
            "today_condition_forecast_jp": "睡眠データが不明のため、睡眠に基づく今日の見通しは判定できません。",
        }
    context = build_sleep_insight_context(
        today_summary=today_summary,
        history_summaries=history_summaries,
    )
    has_today_signal = any(
        value is not None and value != "" for value in context.today_values.values()
    )
    if not has_today_signal:
        logging.info(
            "phase_c_sleep_skipped target_date=%s skip_reason=no_sleep_signal generated_properties=[]",
            target_date,
        )
        return {}
    try:
        result = generate_sleep_insights(target_date=target_date, context=context)
        if result:
            logging.info(
                "phase_c_sleep_generated target_date=%s generated_properties=%s today_values_present_count=%s trend_values_present_count=%s",
                target_date,
                sorted(result.keys()),
                sum(1 for value in context.today_values.values() if value not in (None, "")),
                sum(1 for value in context.trend_values.values() if value not in (None, "")),
            )
        else:
            logging.info(
                "phase_c_sleep_skipped target_date=%s skip_reason=no_sleep_signal generated_properties=[]",
                target_date,
            )
        return result
    except Exception:
        logging.exception("phase_c_sleep_failed target_date=%s", target_date)
        return {}
