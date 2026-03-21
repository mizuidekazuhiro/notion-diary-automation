from __future__ import annotations

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
SAMPLE_DAYS_PER_BUCKET = 5

MINI_SYSTEM_PROMPT = """あなたは Daily Log 分析用の材料整理アシスタントです。
役割は最終助言を書くことではなく、過去30日の材料を構造化して整理することです。

必須ルール:
- 高評価日(Mood 4/5)と低評価日(Mood 1/2)の違いを整理する
- Notes / Diary / Location summary のシグナルを見る
- 記録状況(PFC未記録、Notes未記録、その他記録漏れ)の違いを見る
- 決め打ちの一般論を書かない
- 原因を断定しない
- 相関らしきものは「傾向」「可能性」として扱う
- 意外な関連性候補も残す
- 最終助言文は絶対に書かない
- 出力は構造化テキストにする

出力には必ず次の見出しを含めてください:
1. high_mood_patterns
2. low_mood_patterns
3. differences_summary
4. recording_patterns
5. notes_diary_location_signals
6. hidden_hypotheses
7. today_relevant_points
"""

FINAL_SYSTEM_PROMPT = """あなたは朝の Daily Log レビュー用に Today advice を書くアシスタントです。
役割は、miniモデルの整理結果と今朝の状態を読み、その日に本当に効きそうな論点を選んで自然な日本語で Today advice を作ることです。

必須ルール:
- 一般論に逃げない
- miniの整理結果と今朝の状態を両方見る
- 毎日無理に違うことを書こうとしない
- ただし、その日に本当に効く論点を選ぶ
- 因果を断定しない
- 仮説は仮説として表現する
- 朝時点で未確定の当日情報を前提にしない
- 行動面だけでなく、必要なら記録面の崩れも扱う
- 説教調にしない
- 3〜5文程度の少し長めの自然な日本語
- 出力は本文だけ
"""


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


def _sample_days(items: Sequence[DailyLogSummary], limit: int = SAMPLE_DAYS_PER_BUCKET) -> list[DailyLogSummary]:
    if len(items) <= limit:
        return list(items)
    sorted_items = sorted(items, key=lambda item: item.target_date)
    if limit == 1:
        return [sorted_items[len(sorted_items) // 2]]
    last_index = len(sorted_items) - 1
    indexes = sorted({round(i * last_index / (limit - 1)) for i in range(limit)})
    selected = [sorted_items[index] for index in indexes]
    while len(selected) < limit:
        for item in sorted_items:
            if item not in selected:
                selected.append(item)
            if len(selected) >= limit:
                break
    return selected[:limit]


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "未記録"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_day_sample(summary: DailyLogSummary) -> str:
    meal_photo_flag = "あり" if summary.meal_photos else "なし"
    fields = [
        ("Date", summary.target_date),
        ("Mood", summary.mood or "未記録"),
        ("Mood Score", str(normalize_mood_to_score(summary.mood) or "未記録")),
        ("Sleep Start", summary.sleep_start or "未記録"),
        ("Sleep End", summary.sleep_end or "未記録"),
        ("Sleep Duration", _format_number(summary.sleep_duration_min)),
        ("Sleep Score", _format_number(summary.sleep_score)),
        ("Sleep Heart Rate", _format_number(summary.sleep_heart_rate)),
        ("Deep Duration", _format_number(summary.deep_duration_min)),
        ("REM Duration", _format_number(summary.rem_duration_min)),
        ("Readiness Stars", _format_number(summary.readiness_stars)),
        ("Readiness HRV", _format_number(summary.readiness_hrv)),
        ("Readiness BPM", _format_number(summary.readiness_bpm)),
        ("Baseline HRV", _format_number(summary.baseline_hrv)),
        ("Done Count", _format_number(summary.done_count)),
        ("Drop Count", _format_number(summary.drop_count)),
        ("Expenses Total", _format_number(summary.expenses_total)),
        ("Kcal", _format_number(summary.kcal)),
        ("Protein", _format_number(summary.protein)),
        ("Fat", _format_number(summary.fat)),
        ("Carb", _format_number(summary.carb)),
        ("Notes", summary.notes or "未記録"),
        ("Diary", summary.diary or "未記録"),
        ("Location summary", summary.location_summary or "未記録"),
        ("Weight", _format_number(summary.weight)),
        ("Meal Photos", meal_photo_flag),
    ]
    return "\n".join(f"- {name}: {value}" for name, value in fields)


def _build_today_state(today_summary: DailyLogSummary, recent_summaries: Sequence[DailyLogSummary]) -> dict[str, Any]:
    recent_days = list(recent_summaries[:3])
    return {
        "today_is_morning_incomplete": True,
        "today_sleep": {
            "sleep_start": today_summary.sleep_start or "未記録",
            "sleep_end": today_summary.sleep_end or "未記録",
            "sleep_duration_min": today_summary.sleep_duration_min,
            "sleep_score": today_summary.sleep_score,
            "sleep_heart_rate": today_summary.sleep_heart_rate,
            "deep_duration_min": today_summary.deep_duration_min,
            "rem_duration_min": today_summary.rem_duration_min,
            "readiness_stars": today_summary.readiness_stars,
            "readiness_hrv": today_summary.readiness_hrv,
            "readiness_bpm": today_summary.readiness_bpm,
            "baseline_hrv": today_summary.baseline_hrv,
            "today_condition_forecast_jp": today_summary.today_condition_forecast_jp or "未記録",
            "sleep_analysis_jp": today_summary.sleep_analysis_jp or "未記録",
        },
        "recent_sleep_trend": [
            {
                "date": item.target_date,
                "sleep_duration_min": item.sleep_duration_min,
                "sleep_score": item.sleep_score,
                "readiness_hrv": item.readiness_hrv,
                "readiness_bpm": item.readiness_bpm,
                "mood": item.mood or "未記録",
            }
            for item in recent_days
        ],
        "yesterday_and_recent_behavior": [
            {
                "date": item.target_date,
                "done_count": item.done_count,
                "drop_count": item.drop_count,
                "expenses_total": item.expenses_total,
                "pfc_recorded": all(value is not None for value in (item.kcal, item.protein, item.fat, item.carb)),
                "notes_recorded": bool((item.notes or "").strip()),
            }
            for item in recent_days
        ],
    }


def _build_structured_comparison(history: Sequence[DailyLogSummary]) -> dict[str, Any]:
    scored = [(item, normalize_mood_to_score(item.mood)) for item in history]
    high = [item for item, mood in scored if mood in {4, 5}]
    low = [item for item, mood in scored if mood in {1, 2}]
    middle = [item for item, mood in scored if mood == 3]

    def compare(items: Sequence[DailyLogSummary]) -> dict[str, Any]:
        return {
            "count": len(items),
            "sleep_duration_avg": _mean([item.sleep_duration_min for item in items]),
            "sleep_score_avg": _mean([item.sleep_score for item in items]),
            "done_count_avg": _mean([_safe_float(item.done_count) for item in items]),
            "drop_count_avg": _mean([_safe_float(item.drop_count) for item in items]),
            "expenses_total_avg": _mean([item.expenses_total for item in items]),
            "kcal_avg": _mean([item.kcal for item in items]),
            "protein_avg": _mean([item.protein for item in items]),
            "fat_avg": _mean([item.fat for item in items]),
            "carb_avg": _mean([item.carb for item in items]),
            "notes_recording_rate": _recording_rate(items, lambda item: item.notes),
            "pfc_recording_rate": _recording_rate(
                items,
                lambda item: [item.kcal, item.protein, item.fat, item.carb]
                if all(v is not None for v in (item.kcal, item.protein, item.fat, item.carb))
                else [],
            ),
            "location_summary_rate": _recording_rate(items, lambda item: item.location_summary),
            "diary_rate": _recording_rate(items, lambda item: item.diary),
            "meal_photo_rate": _recording_rate(items, lambda item: item.meal_photos),
        }

    return {
        "counts": {
            "history_days": len(history),
            "high_mood_days": len(high),
            "low_mood_days": len(low),
            "middle_mood_days": len(middle),
            "mood_recorded_days": sum(1 for _, mood in scored if mood is not None),
        },
        "comparisons": {
            "high_mood": compare(high),
            "low_mood": compare(low),
            "middle_mood": compare(middle),
        },
        "high_mood_samples": [_format_day_sample(item) for item in _sample_days(high)],
        "low_mood_samples": [_format_day_sample(item) for item in _sample_days(low)],
        "high_mood_sample_count": min(len(high), SAMPLE_DAYS_PER_BUCKET),
        "low_mood_sample_count": min(len(low), SAMPLE_DAYS_PER_BUCKET),
    }


def _chat_completion(*, model: str, system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "temperature": 0.3,
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
    recent_prior_days = [item for item in history if item.target_date != target_date][:3]
    today_state = _build_today_state(today_summary, recent_prior_days)

    mini_model = os.getenv("TODAY_ADVICE_MINI_MODEL", DEFAULT_MINI_MODEL).strip() or DEFAULT_MINI_MODEL
    final_model = os.getenv("TODAY_ADVICE_FINAL_MODEL", os.getenv("OPENAI_MODEL", DEFAULT_FINAL_MODEL)).strip() or DEFAULT_FINAL_MODEL

    mini_user_prompt = (
        "以下の Daily Log 材料を読んで、最終助言は書かずに材料整理だけをしてください。\n"
        "当日データは朝時点で未完成です。因果は断定せず、傾向と可能性として整理してください。\n\n"
        f"A. 今日朝の状態\n{json.dumps(today_state, ensure_ascii=False, indent=2)}\n\n"
        f"B. 過去30日の構造化情報\n{json.dumps(structured['counts'], ensure_ascii=False, indent=2)}\n"
        f"{json.dumps(structured['comparisons'], ensure_ascii=False, indent=2)}\n\n"
        "C. 生データの日次サンプル\n"
        f"High mood samples ({structured['high_mood_sample_count']}件):\n" + "\n\n".join(structured["high_mood_samples"]) + "\n\n"
        f"Low mood samples ({structured['low_mood_sample_count']}件):\n" + "\n\n".join(structured["low_mood_samples"])
    )
    mini_analysis = _chat_completion(
        model=mini_model,
        system_prompt=MINI_SYSTEM_PROMPT,
        user_prompt=mini_user_prompt,
    )

    final_user_prompt = (
        "以下をもとに、今日の終わりに Mood 4 または 5 を付けやすくするための Today advice を本文だけで作ってください。\n"
        "当日の Done/Drop/PFC/Notes/Mood の完成値はまだ存在しない前提です。\n\n"
        f"今朝の状態:\n{json.dumps(today_state, ensure_ascii=False, indent=2)}\n\n"
        f"過去30日の比較要約:\n{json.dumps(structured['counts'], ensure_ascii=False, indent=2)}\n"
        f"{json.dumps(structured['comparisons'], ensure_ascii=False, indent=2)}\n\n"
        f"mini整理結果:\n{mini_analysis}\n"
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
