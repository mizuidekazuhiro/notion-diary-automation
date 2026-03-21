from __future__ import annotations

import importlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

import requests

from publish.read_daily_log import DailyLogSummary, read_daily_log

OPENAI_TIMEOUT = (5, 90)
DEFAULT_MINI_MODEL = "gpt-4.1-mini"
DEFAULT_FINAL_MODEL = "gpt-4.1"
LOOKBACK_DAYS = 30
RECENT_WINDOW_DAYS = 14
SHORT_WINDOW_DAYS = 7
SAMPLE_DAYS_PER_BUCKET = 5

MINI_SYSTEM_PROMPT = """あなたは Today advice 用の判定JSONを作る前段整理アシスタントです。
役割は、過去30日比較と当日状態から判断材料を整理し、最終本文の元になる判定JSONだけを作ることです。

必須ルール:
- 出力は必ず JSON オブジェクト 1 個のみ。前置きや補足文は禁止
- 最終本文、見出し、メール文面は絶対に書かない
- 当日の diary 本文、過去の日記本文、diary由来要約は使わない
- 日本語の自由記述として参照してよいのは notes のみ
- location summary は構造化された行動コンテキストとして参照してよい
- 睡眠、食事、done、drop、spend、記録有無、notes、location summary、過去30日比較、top_good_days、top_bad_days のみで判断する
- 因果は断定しない。相関は「傾向」「近さ」「可能性」に留める
- 支出から感情を安易に推測しない
- 食事記録がない場合は未記録シグナルとして扱ってよいが、健康状態を断定しない
- タスクが少ない場合も、進捗不足とは断定せず、done/drop/open から言える範囲に留める
- recommended_actions は 1 個または 2 個の短い文字列に絞る
- evidence_used には本文生成に使う根拠を簡潔な配列で残す

必須キー:
- day_type
- main_bottleneck
- priority_theme
- primary_risk
- good_pattern_similarity
- bad_pattern_similarity
- notes_signal
- recording_signal
- evidence_used
- recommended_actions
"""

FINAL_SYSTEM_PROMPT = """あなたは朝メール冒頭に載せる Today advice 本文を書くアシスタントです。
役割は、判定JSONと当日の最小限の事実だけを使い、短めでも密度の高い日本語本文を書くことです。

最優先要件:
- 出力は日本語本文のみ。見出し、タイトル、箇条書き、JSONは禁止
- 2段落以内
- 220〜380字程度
- 一般論は禁止
- 事実 → 解釈 → 今日の優先行動 の順に自然につなぐ
- 行動提案は 1〜2 個に絞る
- 同じ事実の言い換えを繰り返さない
- 読後に「今日は何を優先する日か」が明確に残るようにする

入力制約:
- 当日の diary 本文、過去の日記本文は使わない
- notes 以外の自由記述を捏造しない
- location summary は事実コンテキストとして参照してよい
- 判定JSONにない論点を勝手に増やしすぎない
- 支出から感情を断定しない
- 食事未記録から健康状態を断定しない

禁止例:
- バランスの良い食事を心がけましょう
- 適度に休憩しましょう
- 無理せず過ごしましょう
- 規則正しい生活を意識しましょう

文体:
- メール冒頭にそのまま置ける自然な日本語
- 丁寧だが回りくどくしない
- 1文ごとに意味を進める
"""


def _dump_today_advice_debug_log(*,
    debug_kind: str,
    stage: str,
    target_date: str,
    model: str,
    advice_input: Mapping[str, Any],
    advice_input_summary: Mapping[str, Any],
    prompt_text: str,
) -> None:
    try:
        advice_input_json = json.dumps(advice_input, ensure_ascii=False, indent=2, default=str)
        advice_summary_json = json.dumps(advice_input_summary, ensure_ascii=False, indent=2, default=str)
        print(f"=== TODAY ADVICE {debug_kind} INPUT DATA START ===")
        print(advice_input_json)
        print(f"=== TODAY ADVICE {debug_kind} INPUT DATA END ===")
        print()
        print(f"=== TODAY ADVICE {debug_kind} INPUT SUMMARY START ===")
        print(advice_summary_json)
        print(f"=== TODAY ADVICE {debug_kind} INPUT SUMMARY END ===")
        print()
        print(f"=== TODAY ADVICE {debug_kind} MODEL START ===")
        print(model)
        print(f"=== TODAY ADVICE {debug_kind} MODEL END ===")
        print()
        print(f"=== TODAY ADVICE {debug_kind} TARGET DATE START ===")
        print(target_date)
        print(f"=== TODAY ADVICE {debug_kind} TARGET DATE END ===")
        print()
        print(f"=== TODAY ADVICE {debug_kind} PROMPT START ===")
        print(prompt_text)
        print(f"=== TODAY ADVICE {debug_kind} PROMPT END ===")
    except Exception as exc:
        logging.warning("today_advice_debug_print_failed kind=%s stage=%s error=%s", debug_kind, stage, exc)

    try:
        debug_dir = os.path.join(os.getcwd(), "debug")
        os.makedirs(debug_dir, exist_ok=True)
        debug_payload = {
            "stage": stage,
            "target_date": target_date,
            "model": model,
            "advice_input": advice_input,
            "advice_input_summary": advice_input_summary,
            "prompt_text": prompt_text,
        }
        debug_path = os.path.join(debug_dir, f"today_advice_{stage}_{debug_kind.lower()}_{target_date}.json")
        with open(debug_path, "w", encoding="utf-8") as debug_file:
            json.dump(debug_payload, debug_file, ensure_ascii=False, indent=2, default=str)
        print(f"=== TODAY ADVICE {debug_kind} DEBUG FILE START ===")
        print(debug_path)
        print(f"=== TODAY ADVICE {debug_kind} DEBUG FILE END ===")
    except Exception as exc:
        logging.warning("today_advice_debug_file_failed kind=%s stage=%s error=%s", debug_kind, stage, exc)


def _build_mood_advice_debug_summary(*, history: Sequence[DailyLogSummary], structured: Mapping[str, Any], today_state: Mapping[str, Any], stage: str, evidence_used: Sequence[str], notes_used: bool, prompt_tokens: Optional[int], token_counting_method: str) -> dict[str, Any]:
    counts = structured.get("counts", {}) if isinstance(structured, Mapping) else {}
    return {
        "stage": stage,
        "history_count": len(history),
        "history_dates": [item.target_date for item in history[:LOOKBACK_DAYS]],
        "high_mood_sample_count": structured.get("high_mood_sample_count") if isinstance(structured, Mapping) else None,
        "low_mood_sample_count": structured.get("low_mood_sample_count") if isinstance(structured, Mapping) else None,
        "history_days": counts.get("history_days") if isinstance(counts, Mapping) else None,
        "recent_7d_days": counts.get("recent_7d_days") if isinstance(counts, Mapping) else None,
        "recent_14d_days": counts.get("recent_14d_days") if isinstance(counts, Mapping) else None,
        "high_mood_days": counts.get("high_mood_days") if isinstance(counts, Mapping) else None,
        "low_mood_days": counts.get("low_mood_days") if isinstance(counts, Mapping) else None,
        "today_state_keys": sorted(today_state.keys()) if isinstance(today_state, Mapping) else [],
        "today_sleep_keys": sorted(today_state.get("today_sleep", {}).keys()) if isinstance(today_state, Mapping) and isinstance(today_state.get("today_sleep"), Mapping) else [],
        "today_activity_keys": sorted(today_state.get("today_activity_context", {}).keys()) if isinstance(today_state, Mapping) and isinstance(today_state.get("today_activity_context"), Mapping) else [],
        "comparison_keys": sorted(today_state.get("comparisons", {}).keys()) if isinstance(today_state, Mapping) and isinstance(today_state.get("comparisons"), Mapping) else [],
        "last_30_days_count": counts.get("last_30_days_count") if isinstance(counts, Mapping) else None,
        "top_good_days_count": counts.get("top_good_days_count") if isinstance(counts, Mapping) else None,
        "top_bad_days_count": counts.get("top_bad_days_count") if isinstance(counts, Mapping) else None,
        "notes_used_count": counts.get("notes_used_count") if isinstance(counts, Mapping) else None,
        "diary_used": False,
        "past_diary_used": False,
        "location_summary_used": True,
        "notes_used": notes_used,
        "evidence_used": list(evidence_used),
        "input_tokens": prompt_tokens,
        "token_counting_method": token_counting_method,
    }

@dataclass(frozen=True)
class MoodAdviceResult:
    today_advice: str
    judgment_json: dict[str, Any]
    judgment_text: str
    high_mood_sample_count: int
    low_mood_sample_count: int
    history_count: int


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


def _safe_int(value: object) -> Optional[int]:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return int(round(numeric))


def normalize_mood_to_score(raw_mood: object) -> Optional[int]:
    if raw_mood is None:
        return None
    if isinstance(raw_mood, (int, float)) and not isinstance(raw_mood, bool):
        score = int(round(float(raw_mood)))
        return score if 1 <= score <= 5 else None
    text = _safe_text(raw_mood)
    if not text:
        return None

    normalized = text.replace("☆", "★").replace("⭐", "★").replace("🌟", "★").replace("✩", "★")
    star_count = normalized.count("★") + normalized.count("⭐")
    if 1 <= star_count <= 5:
        return star_count

    digit_match = re.search(r"([1-5])", normalized)
    if digit_match:
        return int(digit_match.group(1))
    return None


def load_daily_logs_for_period(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: str,
    days: int = LOOKBACK_DAYS,
) -> list[DailyLogSummary]:
    base_day = datetime.strptime(target_date, "%Y-%m-%d")
    summaries: list[DailyLogSummary] = []
    for offset in range(days):
        day = (base_day - timedelta(days=offset)).strftime("%Y-%m-%d")
        summary = read_daily_log(
            daily_log_read_url=daily_log_read_url,
            target_date=day,
            bearer_token=bearer_token,
        )
        if summary:
            summaries.append(summary)
    return summaries


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _delta(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base is None:
        return None
    return round(current - base, 2)


def _recording_rate(items: Sequence[DailyLogSummary], extractor: Any) -> Optional[float]:
    if not items:
        return None
    count = 0
    for item in items:
        value = extractor(item)
        if isinstance(value, str):
            if value.strip():
                count += 1
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            count += 1
        elif isinstance(value, list) and value:
            count += 1
    return round(count / len(items), 2)


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "未記録"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _build_metric_snapshot(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    return {
        "sleep_duration_min_avg": _mean([item.sleep_duration_min for item in items]),
        "sleep_score_avg": _mean([item.sleep_score for item in items]),
        "done_count_avg": _mean([_safe_float(item.done_count) for item in items]),
        "drop_count_avg": _mean([_safe_float(item.drop_count) for item in items]),
        "expenses_total_avg": _mean([item.expenses_total for item in items]),
    }


def _trend_direction(values: Sequence[Optional[float]]) -> Optional[str]:
    nums = [float(v) for v in values if v is not None]
    if len(nums) < 3:
        return None
    if nums[0] < nums[1] < nums[2]:
        return "up"
    if nums[0] > nums[1] > nums[2]:
        return "down"
    return None


def _build_today_state(today_summary: DailyLogSummary, recent_summaries: Sequence[DailyLogSummary]) -> dict[str, Any]:
    yesterday = recent_summaries[0] if recent_summaries else None
    recent_7 = list(recent_summaries[:SHORT_WINDOW_DAYS])
    recent_14 = list(recent_summaries[:RECENT_WINDOW_DAYS])
    recent_3_chronological = list(reversed(recent_summaries[:3]))

    recent_7_metrics = _build_metric_snapshot(recent_7)
    recent_14_metrics = _build_metric_snapshot(recent_14)

    today_sleep_duration = _safe_float(today_summary.sleep_duration_min)
    today_sleep_score = _safe_float(today_summary.sleep_score)

    return {
        "today_is_morning_incomplete": True,
        "today_sleep": {
            "sleep_start": today_summary.sleep_start or "未記録",
            "sleep_end": today_summary.sleep_end or "未記録",
            "sleep_duration_min": today_summary.sleep_duration_min,
            "sleep_score": today_summary.sleep_score,
        },
        "today_activity_context": {
            "location_summary": today_summary.location_summary,
            "meal_logged": bool(_safe_text(today_summary.meal_summary) or today_summary.meal_photos),
            "done_count": today_summary.done_count,
            "drop_count": today_summary.drop_count,
            "spend_total": today_summary.expenses_total,
            "notes": today_summary.notes,
            "daily_score": normalize_mood_to_score(today_summary.mood),
        },
        "comparisons": {
            "vs_yesterday": {
                "sleep_duration_min_delta": _delta(today_sleep_duration, _safe_float(yesterday.sleep_duration_min) if yesterday else None),
                "sleep_score_delta": _delta(today_sleep_score, _safe_float(yesterday.sleep_score) if yesterday else None),
                "done_count_delta": _delta(_safe_float(today_summary.done_count), _safe_float(yesterday.done_count) if yesterday else None),
                "drop_count_delta": _delta(_safe_float(today_summary.drop_count), _safe_float(yesterday.drop_count) if yesterday else None),
                "spend_total_delta": _delta(_safe_float(today_summary.expenses_total), _safe_float(yesterday.expenses_total) if yesterday else None),
            },
            "vs_recent_7d_avg": {
                "sleep_duration_min_delta": _delta(today_sleep_duration, recent_7_metrics["sleep_duration_min_avg"]),
                "sleep_score_delta": _delta(today_sleep_score, recent_7_metrics["sleep_score_avg"]),
                "done_count_delta": _delta(_safe_float(today_summary.done_count), recent_7_metrics["done_count_avg"]),
                "drop_count_delta": _delta(_safe_float(today_summary.drop_count), recent_7_metrics["drop_count_avg"]),
                "spend_total_delta": _delta(_safe_float(today_summary.expenses_total), recent_7_metrics["expenses_total_avg"]),
            },
            "recent_7d_avg": recent_7_metrics,
            "recent_14d_avg": recent_14_metrics,
        },
        "recent_3day_trend": {
            "sleep_duration_min": _trend_direction([item.sleep_duration_min for item in recent_3_chronological]),
            "sleep_score": _trend_direction([item.sleep_score for item in recent_3_chronological]),
            "done_count": _trend_direction([_safe_float(item.done_count) for item in recent_3_chronological]),
            "drop_count": _trend_direction([_safe_float(item.drop_count) for item in recent_3_chronological]),
            "spend_total": _trend_direction([_safe_float(item.expenses_total) for item in recent_3_chronological]),
        },
    }


def _build_day_record(summary: DailyLogSummary) -> dict[str, Any]:
    return {
        "date": summary.target_date,
        "sleep_start": summary.sleep_start,
        "sleep_end": summary.sleep_end,
        "sleep_duration_min": summary.sleep_duration_min,
        "sleep_score": summary.sleep_score,
        "location_summary": summary.location_summary,
        "meal_summary": summary.meal_summary,
        "meal_logged": bool(_safe_text(summary.meal_summary) or summary.meal_photos),
        "done_count": summary.done_count,
        "drop_count": summary.drop_count,
        "spend_total": summary.expenses_total,
        "notes": summary.notes,
        "daily_score": normalize_mood_to_score(summary.mood),
    }


def _select_top_days(items: Sequence[DailyLogSummary], *, descending: bool, limit: int = SAMPLE_DAYS_PER_BUCKET) -> list[DailyLogSummary]:
    scored_items = []
    for item in items:
        score = normalize_mood_to_score(item.mood)
        if score is None:
            continue
        scored_items.append((item, score, _safe_float(item.sleep_score) or -1.0, _safe_float(item.done_count) or -1.0, item.target_date))
    if descending:
        filtered = [entry for entry in scored_items if entry[1] >= 4]
        filtered.sort(key=lambda entry: (-entry[1], -(entry[2]), -(entry[3]), entry[4]))
    else:
        filtered = [entry for entry in scored_items if entry[1] <= 2]
        filtered.sort(key=lambda entry: (entry[1], -(entry[2]), -(entry[3]), entry[4]))
    if len(filtered) <= limit:
        return [item for item, *_ in filtered]
    step = max(1, len(filtered) // limit)
    selected: list[DailyLogSummary] = []
    for index in range(0, len(filtered), step):
        selected.append(filtered[index][0])
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for item, *_ in filtered:
            if item not in selected:
                selected.append(item)
            if len(selected) >= limit:
                break
    return selected[:limit]


def _build_structured_comparison(history: Sequence[DailyLogSummary]) -> dict[str, Any]:
    scored = [(item, normalize_mood_to_score(item.mood)) for item in history]
    high = [item for item, mood in scored if mood in {4, 5}]
    low = [item for item, mood in scored if mood in {1, 2}]
    middle = [item for item, mood in scored if mood == 3]
    recent_7 = list(history[:SHORT_WINDOW_DAYS])
    recent_14 = list(history[:RECENT_WINDOW_DAYS])
    top_good_days = _select_top_days(history, descending=True)
    top_bad_days = _select_top_days(history, descending=False)
    last_30_days = [_build_day_record(item) for item in history[:LOOKBACK_DAYS]]

    def compare(items: Sequence[DailyLogSummary]) -> dict[str, Any]:
        return {
            "count": len(items),
            **_build_metric_snapshot(items),
            "notes_recording_rate": _recording_rate(items, lambda item: item.notes),
            "meal_logged_rate": _recording_rate(items, lambda item: [item.meal_summary] if _safe_text(item.meal_summary) else item.meal_photos),
        }

    notes_used_count = sum(1 for item in history if _safe_text(item.notes))
    return {
        "counts": {
            "history_days": len(history),
            "recent_7d_days": len(recent_7),
            "recent_14d_days": len(recent_14),
            "high_mood_days": len(high),
            "low_mood_days": len(low),
            "middle_mood_days": len(middle),
            "mood_recorded_days": sum(1 for _, mood in scored if mood is not None),
            "last_30_days_count": len(last_30_days),
            "top_good_days_count": len(top_good_days),
            "top_bad_days_count": len(top_bad_days),
            "notes_used_count": notes_used_count,
            "diary_used": False,
        },
        "comparisons": {
            "recent_7d": compare(recent_7),
            "recent_14d": compare(recent_14),
            "high_mood": compare(high),
            "low_mood": compare(low),
            "middle_mood": compare(middle),
            "good_vs_bad_delta": {
                "sleep_duration_min": _delta(_build_metric_snapshot(top_good_days)["sleep_duration_min_avg"], _build_metric_snapshot(top_bad_days)["sleep_duration_min_avg"]),
                "sleep_score": _delta(_build_metric_snapshot(top_good_days)["sleep_score_avg"], _build_metric_snapshot(top_bad_days)["sleep_score_avg"]),
                "done_count": _delta(_build_metric_snapshot(top_good_days)["done_count_avg"], _build_metric_snapshot(top_bad_days)["done_count_avg"]),
                "drop_count": _delta(_build_metric_snapshot(top_good_days)["drop_count_avg"], _build_metric_snapshot(top_bad_days)["drop_count_avg"]),
                "spend_total": _delta(_build_metric_snapshot(top_good_days)["expenses_total_avg"], _build_metric_snapshot(top_bad_days)["expenses_total_avg"]),
            },
        },
        "last_30_days_summary": {
            "daily_records": last_30_days,
            "aggregates": {
                "all_days": compare(history),
                "recent_7d": compare(recent_7),
                "recent_14d": compare(recent_14),
                "top_good_days": compare(top_good_days),
                "top_bad_days": compare(top_bad_days),
            },
        },
        "top_good_days": [_build_day_record(item) for item in top_good_days],
        "top_bad_days": [_build_day_record(item) for item in top_bad_days],
        "high_mood_sample_count": len(top_good_days),
        "low_mood_sample_count": len(top_bad_days),
    }


def _build_chat_messages(*, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _count_input_tokens(*, model: str, messages: Sequence[Mapping[str, Any]]) -> tuple[Optional[int], str]:
    tiktoken_spec = importlib.util.find_spec("tiktoken")
    if tiktoken_spec is not None:
        tiktoken = importlib.import_module("tiktoken")
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("o200k_base")
        total = 0
        for message in messages:
            total += 4
            total += len(encoding.encode(str(message.get("role", ""))))
            total += len(encoding.encode(str(message.get("content", ""))))
        total += 2
        return total, "tiktoken"
    raw_text = json.dumps(list(messages), ensure_ascii=False)
    return max(1, len(raw_text) // 4), "estimated_chars_div4"


def _chat_completion(*, model: str, system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    messages = _build_chat_messages(system_prompt=system_prompt, user_prompt=user_prompt)
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "temperature": 0.3,
            "messages": messages,
        },
        timeout=OPENAI_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI response did not include content")
    return content.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise RuntimeError("Stage 1 response did not contain a JSON object")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise RuntimeError("Stage 1 response must be a JSON object")
    return data


def _normalize_judgment_json(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    actions = normalized.get("recommended_actions")
    if isinstance(actions, list):
        clean_actions = [str(item).strip() for item in actions if str(item).strip()][:2]
    elif actions is None:
        clean_actions = []
    else:
        action_text = str(actions).strip()
        clean_actions = [action_text] if action_text else []
    normalized["recommended_actions"] = clean_actions
    evidence = normalized.get("evidence_used")
    if isinstance(evidence, list):
        normalized["evidence_used"] = [str(item).strip() for item in evidence if str(item).strip()]
    elif evidence is None:
        normalized["evidence_used"] = []
    else:
        evidence_text = str(evidence).strip()
        normalized["evidence_used"] = [evidence_text] if evidence_text else []
    for key in (
        "day_type",
        "main_bottleneck",
        "priority_theme",
        "primary_risk",
        "good_pattern_similarity",
        "bad_pattern_similarity",
        "notes_signal",
        "recording_signal",
    ):
        value = normalized.get(key)
        normalized[key] = "" if value is None else str(value).strip()
    return normalized


def generate_today_advice(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: str,
) -> Optional[MoodAdviceResult]:
    history = load_daily_logs_for_period(
        daily_log_read_url=daily_log_read_url,
        bearer_token=bearer_token,
        target_date=target_date,
        days=LOOKBACK_DAYS,
    )
    if not history:
        logging.info("Skipping Today advice because no Daily Log history is available. target_date=%s", target_date)
        return None

    today_summary = next((item for item in history if item.target_date == target_date), history[0])
    structured = _build_structured_comparison(history)
    recent_prior_days = [item for item in history if item.target_date != target_date][:RECENT_WINDOW_DAYS]
    today_state = _build_today_state(today_summary, recent_prior_days)

    mini_model = os.getenv("TODAY_ADVICE_MINI_MODEL", DEFAULT_MINI_MODEL).strip() or DEFAULT_MINI_MODEL
    final_model = os.getenv("TODAY_ADVICE_FINAL_MODEL", os.getenv("OPENAI_MODEL", DEFAULT_FINAL_MODEL)).strip() or DEFAULT_FINAL_MODEL
    notes_used = bool(_safe_text(today_summary.notes))

    judgment_input = {
        "today_state": today_state,
        "structured_comparison": {
            "counts": structured["counts"],
            "comparisons": structured["comparisons"],
            "last_30_days_summary": structured["last_30_days_summary"],
        },
        "top_good_days": structured["top_good_days"],
        "top_bad_days": structured["top_bad_days"],
        "input_policy": {
            "diary_used": False,
            "past_diary_used": False,
            "location_summary_used": True,
            "notes_used": notes_used,
        },
    }
    judgment_user_prompt = f"""以下の材料から、Today advice の本文を書く前段として判定JSONだけを返してください。
出力は JSON オブジェクト 1 個のみで、本文・見出し・説明は禁止です。
recommended_actions は 1〜2 個、evidence_used は本文生成に使う根拠だけを短く列挙してください。

A. 今日の状態
{json.dumps(today_state, ensure_ascii=False, indent=2)}

B. 過去30日比較
{json.dumps(structured["comparisons"], ensure_ascii=False, indent=2)}

C. 過去30日の集計サマリ
{json.dumps(structured["last_30_days_summary"], ensure_ascii=False, indent=2)}

D. 良い日サンプル
{json.dumps(structured["top_good_days"], ensure_ascii=False, indent=2)}

E. 悪い日サンプル
{json.dumps(structured["top_bad_days"], ensure_ascii=False, indent=2)}"""
    judgment_messages = _build_chat_messages(system_prompt=MINI_SYSTEM_PROMPT, user_prompt=judgment_user_prompt)
    judgment_prompt_tokens, judgment_token_method = _count_input_tokens(model=mini_model, messages=judgment_messages)
    logging.info(
        "today_advice_stage_input target_date=%s stage=judgment model=%s diary_used=%s past_diary_used=%s location_summary_used=%s notes_used=%s input_tokens=%s token_counting_method=%s evidence_used=%s",
        target_date,
        mini_model,
        False,
        False,
        True,
        notes_used,
        judgment_prompt_tokens,
        judgment_token_method,
        [],
    )
    _dump_today_advice_debug_log(
        debug_kind="MOOD_JUDGMENT",
        stage="judgment",
        target_date=target_date,
        model=mini_model,
        advice_input=judgment_input,
        advice_input_summary=_build_mood_advice_debug_summary(
            history=history,
            structured=structured,
            today_state=today_state,
            stage="judgment",
            evidence_used=[],
            notes_used=notes_used,
            prompt_tokens=judgment_prompt_tokens,
            token_counting_method=judgment_token_method,
        ),
        prompt_text=f"[system]\n{MINI_SYSTEM_PROMPT}\n\n[user]\n{judgment_user_prompt}",
    )
    try:
        judgment_text = _chat_completion(
            model=mini_model,
            system_prompt=MINI_SYSTEM_PROMPT,
            user_prompt=judgment_user_prompt,
        )
        judgment_json = _normalize_judgment_json(_extract_json_object(judgment_text))
    except Exception as exc:
        raise RuntimeError(f"today_advice stage 1 judgment failed: {exc}") from exc
    evidence_used = judgment_json.get("evidence_used", []) if isinstance(judgment_json.get("evidence_used"), list) else []

    final_input = {
        "judgment_json": judgment_json,
        "today_facts": {
            "today_sleep": today_state.get("today_sleep", {}),
            "today_activity_context": today_state.get("today_activity_context", {}),
            "comparisons": today_state.get("comparisons", {}),
            "recent_3day_trend": today_state.get("recent_3day_trend", {}),
        },
        "input_policy": {
            "diary_used": False,
            "past_diary_used": False,
            "location_summary_used": True,
            "notes_used": notes_used,
        },
    }
    final_user_prompt = f"""以下の判定JSONと当日の最小限の事実だけを使って、Today advice の本文を書いてください。
出力は見出しなしの日本語本文のみ、2段落以内、220〜380字程度です。
事実 → 解釈 → 今日の優先行動 の順で、行動提案は recommended_actions にある 1〜2 個へ絞ってください。

判定JSON:
{json.dumps(judgment_json, ensure_ascii=False, indent=2)}

当日の事実:
{json.dumps(final_input["today_facts"], ensure_ascii=False, indent=2)}"""
    final_messages = _build_chat_messages(system_prompt=FINAL_SYSTEM_PROMPT, user_prompt=final_user_prompt)
    final_prompt_tokens, final_token_method = _count_input_tokens(model=final_model, messages=final_messages)
    logging.info(
        "today_advice_stage_input target_date=%s stage=final model=%s diary_used=%s past_diary_used=%s location_summary_used=%s notes_used=%s input_tokens=%s token_counting_method=%s evidence_used=%s",
        target_date,
        final_model,
        False,
        False,
        True,
        notes_used,
        final_prompt_tokens,
        final_token_method,
        evidence_used,
    )
    _dump_today_advice_debug_log(
        debug_kind="MOOD_FINAL",
        stage="final",
        target_date=target_date,
        model=final_model,
        advice_input=final_input,
        advice_input_summary=_build_mood_advice_debug_summary(
            history=history,
            structured=structured,
            today_state=today_state,
            stage="final",
            evidence_used=evidence_used,
            notes_used=notes_used,
            prompt_tokens=final_prompt_tokens,
            token_counting_method=final_token_method,
        ),
        prompt_text=f"[system]\n{FINAL_SYSTEM_PROMPT}\n\n[user]\n{final_user_prompt}",
    )
    try:
        today_advice = _chat_completion(
            model=final_model,
            system_prompt=FINAL_SYSTEM_PROMPT,
            user_prompt=final_user_prompt,
        )
    except Exception as exc:
        raise RuntimeError(f"today_advice stage 2 final writing failed: {exc}") from exc

    return MoodAdviceResult(
        today_advice=today_advice,
        judgment_json=judgment_json,
        judgment_text=judgment_text,
        high_mood_sample_count=structured["high_mood_sample_count"],
        low_mood_sample_count=structured["low_mood_sample_count"],
        history_count=len(history),
    )
