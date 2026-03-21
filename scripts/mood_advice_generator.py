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

MINI_SYSTEM_PROMPT = """あなたは Daily Log 分析用の材料整理アシスタントです。
役割は最終助言を書くことではなく、過去30日の材料を構造化して整理することです。

必須ルール:
- 過去30日全体の構造化サマリを踏まえて整理する
- 評価が高い日5件と低い日5件の差を比較する
- 当日の diary 本文や過去の日記本文は参照しない
- 日本語の自由記述として参照してよいのは notes のみ
- 睡眠、食事、タスク、支出、記録状況、notes のうち根拠のあるものだけを使う
- 決め打ちの一般論を書かない
- 原因を断定しない
- 相関らしきものは「傾向」「可能性」として扱う
- データ不足の項目は不足として扱い、補完しない
- 最終助言文は絶対に書かない
- 出力は構造化テキストにする

出力には必ず次の見出しを含めてください:
1. recent_trends
2. top_good_days_patterns
3. top_bad_days_patterns
4. good_vs_bad_differences
5. notes_signals
6. recording_patterns
7. hidden_hypotheses
8. today_relevant_points
"""

FINAL_SYSTEM_PROMPT = """あなたは朝の Daily Log レビュー用に Today advice を書くアシスタントです。
役割は、miniモデルの整理結果と今朝の状態を読み、その日に本当に効きそうな論点を選んで、メール本文の冒頭にそのまま載せられる品質の日本語で Today advice を作ることです。

最優先要件:
- 出力タイトルは必ず `Today advice` にする
- 出力は以下の4部構成に固定する
  1. Today advice
  2. 直近の傾向
  3. 本日の状態
  4. 本日の進め方
  5. 総括
- 見出しは上記の自然な日本語をそのまま使い、口語的な見出しにしない
- 全体は400〜700字程度を目安にし、短すぎる出力にしない
- 各セクションは箇条書きではなく、3〜5文程度の自然な連続文で書く
- 総括は最後に1文で簡潔にまとめる

内容ルール:
- 一般論に逃げない
- 必ず入力データに基づいて書く
- 過去30日の構造化サマリと、評価が高い日5件・低い日5件の比較を必ず踏まえる
- 今日は直近30日の中でどのパターンに近いかをまず判断する
- 良い日 / 悪い日の差分を踏まえて、今日の進め方を具体化する
- 今日の睡眠時間、就寝時刻、起床時刻、睡眠スコア、前日比、直近平均との差分、食事記録、タスク状況、支出、notes を優先する
- 良い点と注意点の両方を書く
- 事実 → 解釈 → 行動提案 の順で自然につなぐ
- 行動提案は時間帯や優先順位がわかる実行可能な粒度で書く
- 当日の diary 本文や過去の日記本文は参照しない
- 日本語自由記述として扱ってよいのは notes のみ
- データから言えないことは断定しない
- 医療断定や過剰な励ましを避ける
- 禁止表現: 「バランスの良い食事を心がけましょう」「適度に休憩しましょう」「無理せず過ごしましょう」「規則正しい生活を意識しましょう」「体調に気をつけましょう」など、入力データと結びつかない抽象的な一般論
- 「〜するとよいでしょう」だけが続く単調な文体にしない

文体:
- 丁寧で自然な日本語
- 口語すぎず、硬すぎず、読みやすい
- レポートと助言文の中間の文体
- 観測事実と解釈を明確に分けつつ、読んで自然につながる文章にする
"""


def _dump_today_advice_debug_log(*,
    debug_kind: str,
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
        logging.warning("today_advice_debug_print_failed kind=%s error=%s", debug_kind, exc)

    try:
        debug_dir = os.path.join(os.getcwd(), "debug")
        os.makedirs(debug_dir, exist_ok=True)
        debug_payload = {
            "target_date": target_date,
            "model": model,
            "advice_input": advice_input,
            "advice_input_summary": advice_input_summary,
            "prompt_text": prompt_text,
        }
        debug_path = os.path.join(debug_dir, f"today_advice_{debug_kind.lower()}_{target_date}.json")
        with open(debug_path, "w", encoding="utf-8") as debug_file:
            json.dump(debug_payload, debug_file, ensure_ascii=False, indent=2, default=str)
        print(f"=== TODAY ADVICE {debug_kind} DEBUG FILE START ===")
        print(debug_path)
        print(f"=== TODAY ADVICE {debug_kind} DEBUG FILE END ===")
    except Exception as exc:
        logging.warning("today_advice_debug_file_failed kind=%s error=%s", debug_kind, exc)


def _build_mood_advice_debug_summary(*, history: Sequence[DailyLogSummary], structured: Mapping[str, Any], today_state: Mapping[str, Any], has_mini_analysis: bool, prompt_tokens: Optional[int], token_counting_method: str) -> dict[str, Any]:
    counts = structured.get("counts", {}) if isinstance(structured, Mapping) else {}
    return {
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
        "has_mini_analysis": has_mini_analysis,
        "last_30_days_count": counts.get("last_30_days_count") if isinstance(counts, Mapping) else None,
        "top_good_days_count": counts.get("top_good_days_count") if isinstance(counts, Mapping) else None,
        "top_bad_days_count": counts.get("top_bad_days_count") if isinstance(counts, Mapping) else None,
        "notes_used_count": counts.get("notes_used_count") if isinstance(counts, Mapping) else None,
        "diary_used": False,
        "input_tokens": prompt_tokens,
        "token_counting_method": token_counting_method,
    }

@dataclass(frozen=True)
class MoodAdviceResult:
    today_advice: str
    mini_analysis: str
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
            "meal_summary": today_summary.meal_summary,
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
        filtered.sort(key=lambda entry: (entry[1], entry[2], entry[3], entry[4]), reverse=True)
    else:
        filtered = [entry for entry in scored_items if entry[1] <= 2]
        filtered.sort(key=lambda entry: (entry[1], -(entry[2]), -(entry[3]), entry[4]))
    return [item for item, *_ in filtered[:limit]]


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

    mini_user_prompt = (
        "以下の Daily Log 材料を読んで、最終助言は書かずに材料整理だけをしてください。\n"
        "当日データは朝時点で未完成です。因果は断定せず、傾向と可能性として整理してください。\n"
        "当日の diary 本文や過去の日記本文は参照禁止です。日本語自由記述として参照してよいのは notes のみです。\n"
        "過去30日の構造化サマリと、評価が高い日5件・低い日5件の比較を必ず使ってください。\n\n"
        f"A. 今日朝の状態\n{json.dumps(today_state, ensure_ascii=False, indent=2)}\n\n"
        f"B. 過去30日の構造化サマリ\n{json.dumps(structured['last_30_days_summary'], ensure_ascii=False, indent=2)}\n\n"
        f"C. 評価が高い日5件の生データ\n{json.dumps(structured['top_good_days'], ensure_ascii=False, indent=2)}\n\n"
        f"D. 評価が低い日5件の生データ\n{json.dumps(structured['top_bad_days'], ensure_ascii=False, indent=2)}"
    )
    mini_messages = _build_chat_messages(system_prompt=MINI_SYSTEM_PROMPT, user_prompt=mini_user_prompt)
    mini_prompt_tokens, mini_token_method = _count_input_tokens(model=mini_model, messages=mini_messages)
    logging.info(
        "today_advice_input_metrics target_date=%s phase=mini input_tokens=%s token_counting_method=%s last_30_days_count=%s top_good_days_count=%s top_bad_days_count=%s notes_used_count=%s diary_used=%s",
        target_date,
        mini_prompt_tokens,
        mini_token_method,
        structured["counts"].get("last_30_days_count"),
        structured["counts"].get("top_good_days_count"),
        structured["counts"].get("top_bad_days_count"),
        structured["counts"].get("notes_used_count"),
        False,
    )
    mini_advice_input = {
        "today_state": today_state,
        "last_30_days_summary": structured["last_30_days_summary"],
        "top_good_days": structured["top_good_days"],
        "top_bad_days": structured["top_bad_days"],
        "diary_used": False,
    }
    _dump_today_advice_debug_log(
        debug_kind="MOOD_MINI",
        target_date=target_date,
        model=mini_model,
        advice_input=mini_advice_input,
        advice_input_summary=_build_mood_advice_debug_summary(
            history=history,
            structured=structured,
            today_state=today_state,
            has_mini_analysis=False,
            prompt_tokens=mini_prompt_tokens,
            token_counting_method=mini_token_method,
        ),
        prompt_text=f"[system]\n{MINI_SYSTEM_PROMPT}\n\n[user]\n{mini_user_prompt}",
    )
    mini_analysis = _chat_completion(
        model=mini_model,
        system_prompt=MINI_SYSTEM_PROMPT,
        user_prompt=mini_user_prompt,
    )

    final_user_prompt = (
        "以下をもとに、Today advice を指定の構成どおりに作成してください。\n"
        "当日の diary 本文や過去の日記本文は参照禁止です。日本語自由記述として扱ってよいのは notes のみです。\n"
        "過去30日の構造化サマリと、評価が高い日5件・低い日5件との比較を必ず踏まえてください。\n"
        "今日は直近30日の中でどのパターンに近いかを判断し、良い日 / 悪い日の差分を踏まえて、今日の進め方を具体化してください。\n"
        "長すぎず、中身のある文章にしてください。\n\n"
        f"今朝の状態:\n{json.dumps(today_state, ensure_ascii=False, indent=2)}\n\n"
        f"過去30日の構造化サマリ:\n{json.dumps(structured['last_30_days_summary'], ensure_ascii=False, indent=2)}\n\n"
        f"評価が高い日5件の生データ:\n{json.dumps(structured['top_good_days'], ensure_ascii=False, indent=2)}\n\n"
        f"評価が低い日5件の生データ:\n{json.dumps(structured['top_bad_days'], ensure_ascii=False, indent=2)}\n\n"
        f"mini整理結果:\n{mini_analysis}\n"
    )
    final_messages = _build_chat_messages(system_prompt=FINAL_SYSTEM_PROMPT, user_prompt=final_user_prompt)
    final_prompt_tokens, final_token_method = _count_input_tokens(model=final_model, messages=final_messages)
    logging.info(
        "today_advice_input_metrics target_date=%s phase=final input_tokens=%s token_counting_method=%s last_30_days_count=%s top_good_days_count=%s top_bad_days_count=%s notes_used_count=%s diary_used=%s",
        target_date,
        final_prompt_tokens,
        final_token_method,
        structured["counts"].get("last_30_days_count"),
        structured["counts"].get("top_good_days_count"),
        structured["counts"].get("top_bad_days_count"),
        structured["counts"].get("notes_used_count"),
        False,
    )
    final_advice_input = {
        "today_state": today_state,
        "last_30_days_summary": structured["last_30_days_summary"],
        "top_good_days": structured["top_good_days"],
        "top_bad_days": structured["top_bad_days"],
        "mini_analysis": mini_analysis,
        "diary_used": False,
    }
    _dump_today_advice_debug_log(
        debug_kind="MOOD_FINAL",
        target_date=target_date,
        model=final_model,
        advice_input=final_advice_input,
        advice_input_summary=_build_mood_advice_debug_summary(
            history=history,
            structured=structured,
            today_state=today_state,
            has_mini_analysis=True,
            prompt_tokens=final_prompt_tokens,
            token_counting_method=final_token_method,
        ),
        prompt_text=f"[system]\n{FINAL_SYSTEM_PROMPT}\n\n[user]\n{final_user_prompt}",
    )
    today_advice = _chat_completion(
        model=final_model,
        system_prompt=FINAL_SYSTEM_PROMPT,
        user_prompt=final_user_prompt,
    )

    return MoodAdviceResult(
        today_advice=today_advice,
        mini_analysis=mini_analysis,
        high_mood_sample_count=structured["high_mood_sample_count"],
        low_mood_sample_count=structured["low_mood_sample_count"],
        history_count=len(history),
    )
