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
RECENT_WINDOW_DAYS = 14
SHORT_WINDOW_DAYS = 7
SAMPLE_DAYS_PER_BUCKET = 5

MINI_SYSTEM_PROMPT = """あなたは Daily Log 分析用の材料整理アシスタントです。
役割は最終助言を書くことではなく、過去30日の材料を構造化して整理することです。

必須ルール:
- 高評価日(Mood 4/5)と低評価日(Mood 1/2)の違いを整理する
- 直近7日〜14日の変化と、直近3日連続の流れがあれば整理する
- 睡眠、活動、食事、体調メモ、タスク、記録状況のうち根拠のあるものだけを使う
- Notes / Diary / Location summary のシグナルを見る
- 決め打ちの一般論を書かない
- 原因を断定しない
- 相関らしきものは「傾向」「可能性」として扱う
- 意外な関連性候補も残す
- データ不足の項目は不足として扱い、補完しない
- 最終助言文は絶対に書かない
- 出力は構造化テキストにする

出力には必ず次の見出しを含めてください:
1. recent_trends
2. high_mood_patterns
3. low_mood_patterns
4. differences_summary
5. recording_patterns
6. notes_diary_location_signals
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
- 直近7日〜14日のデータから根拠がある傾向だけを書く
- 今日の睡眠時間、就寝時刻、起床時刻、睡眠スコア、前日比、直近平均との差分、予定、未処理タスクなど使える情報を優先する
- 良い点と注意点の両方を書く
- 事実 → 解釈 → 行動提案 の順で自然につなぐ
- 行動提案は時間帯や優先順位がわかる実行可能な粒度で書く
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


def _build_mood_advice_debug_summary(*, history: Sequence[DailyLogSummary], structured: Mapping[str, Any], today_state: Mapping[str, Any], has_mini_analysis: bool) -> dict[str, Any]:
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


def _build_metric_snapshot(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    return {
        "sleep_duration_min_avg": _mean([item.sleep_duration_min for item in items]),
        "sleep_score_avg": _mean([item.sleep_score for item in items]),
        "readiness_hrv_avg": _mean([item.readiness_hrv for item in items]),
        "readiness_bpm_avg": _mean([item.readiness_bpm for item in items]),
        "done_count_avg": _mean([_safe_float(item.done_count) for item in items]),
        "drop_count_avg": _mean([_safe_float(item.drop_count) for item in items]),
        "expenses_total_avg": _mean([item.expenses_total for item in items]),
        "kcal_avg": _mean([item.kcal for item in items]),
        "protein_avg": _mean([item.protein for item in items]),
        "fat_avg": _mean([item.fat for item in items]),
        "carb_avg": _mean([item.carb for item in items]),
        "weight_avg": _mean([item.weight for item in items]),
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
    today_readiness_hrv = _safe_float(today_summary.readiness_hrv)
    today_readiness_bpm = _safe_float(today_summary.readiness_bpm)

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
        "today_activity_context": {
            "location_summary": today_summary.location_summary,
            "activity_summary": today_summary.activity_summary,
            "meal_summary": today_summary.meal_summary,
            "done_count": today_summary.done_count,
            "drop_count": today_summary.drop_count,
            "done_tasks": list(today_summary.done_tasks),
            "drop_tasks": list(today_summary.drop_tasks),
            "notes": today_summary.notes,
            "diary": today_summary.diary,
        },
        "comparisons": {
            "vs_yesterday": {
                "sleep_duration_min_delta": _delta(today_sleep_duration, _safe_float(yesterday.sleep_duration_min) if yesterday else None),
                "sleep_score_delta": _delta(today_sleep_score, _safe_float(yesterday.sleep_score) if yesterday else None),
                "readiness_hrv_delta": _delta(today_readiness_hrv, _safe_float(yesterday.readiness_hrv) if yesterday else None),
                "readiness_bpm_delta": _delta(today_readiness_bpm, _safe_float(yesterday.readiness_bpm) if yesterday else None),
            },
            "vs_recent_7d_avg": {
                "sleep_duration_min_delta": _delta(today_sleep_duration, recent_7_metrics["sleep_duration_min_avg"]),
                "sleep_score_delta": _delta(today_sleep_score, recent_7_metrics["sleep_score_avg"]),
                "readiness_hrv_delta": _delta(today_readiness_hrv, recent_7_metrics["readiness_hrv_avg"]),
                "readiness_bpm_delta": _delta(today_readiness_bpm, recent_7_metrics["readiness_bpm_avg"]),
            },
            "recent_7d_avg": recent_7_metrics,
            "recent_14d_avg": recent_14_metrics,
        },
        "recent_3day_trend": {
            "sleep_duration_min": _trend_direction([item.sleep_duration_min for item in recent_3_chronological]),
            "sleep_score": _trend_direction([item.sleep_score for item in recent_3_chronological]),
            "done_count": _trend_direction([_safe_float(item.done_count) for item in recent_3_chronological]),
            "drop_count": _trend_direction([_safe_float(item.drop_count) for item in recent_3_chronological]),
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
            for item in recent_summaries[:7]
        ],
        "recent_behavior_trend": [
            {
                "date": item.target_date,
                "done_count": item.done_count,
                "drop_count": item.drop_count,
                "expenses_total": item.expenses_total,
                "kcal": item.kcal,
                "protein": item.protein,
                "fat": item.fat,
                "carb": item.carb,
                "pfc_recorded": all(value is not None for value in (item.kcal, item.protein, item.fat, item.carb)),
                "notes_recorded": bool((item.notes or "").strip()),
            }
            for item in recent_summaries[:7]
        ],
    }


def _build_structured_comparison(history: Sequence[DailyLogSummary]) -> dict[str, Any]:
    scored = [(item, normalize_mood_to_score(item.mood)) for item in history]
    high = [item for item, mood in scored if mood in {4, 5}]
    low = [item for item, mood in scored if mood in {1, 2}]
    middle = [item for item, mood in scored if mood == 3]
    recent_7 = list(history[:SHORT_WINDOW_DAYS])
    recent_14 = list(history[:RECENT_WINDOW_DAYS])

    def compare(items: Sequence[DailyLogSummary]) -> dict[str, Any]:
        return {
            "count": len(items),
            **_build_metric_snapshot(items),
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
            "recent_7d_days": len(recent_7),
            "recent_14d_days": len(recent_14),
            "high_mood_days": len(high),
            "low_mood_days": len(low),
            "middle_mood_days": len(middle),
            "mood_recorded_days": sum(1 for _, mood in scored if mood is not None),
        },
        "comparisons": {
            "recent_7d": compare(recent_7),
            "recent_14d": compare(recent_14),
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
    recent_prior_days = [item for item in history if item.target_date != target_date][:RECENT_WINDOW_DAYS]
    today_state = _build_today_state(today_summary, recent_prior_days)

    mini_model = os.getenv("TODAY_ADVICE_MINI_MODEL", DEFAULT_MINI_MODEL).strip() or DEFAULT_MINI_MODEL
    final_model = os.getenv("TODAY_ADVICE_FINAL_MODEL", os.getenv("OPENAI_MODEL", DEFAULT_FINAL_MODEL)).strip() or DEFAULT_FINAL_MODEL

    mini_user_prompt = (
        "以下の Daily Log 材料を読んで、最終助言は書かずに材料整理だけをしてください。\n"
        "当日データは朝時点で未完成です。因果は断定せず、傾向と可能性として整理してください。\n"
        "特に、直近7日平均、前日比、直近平均との差分、直近3日連続の流れが使える箇所を優先して整理してください。\n\n"
        f"A. 今日朝の状態\n{json.dumps(today_state, ensure_ascii=False, indent=2)}\n\n"
        f"B. 過去30日の構造化情報\n{json.dumps(structured['counts'], ensure_ascii=False, indent=2)}\n"
        f"{json.dumps(structured['comparisons'], ensure_ascii=False, indent=2)}\n\n"
        "C. 生データの日次サンプル\n"
        f"High mood samples ({structured['high_mood_sample_count']}件):\n" + "\n\n".join(structured["high_mood_samples"]) + "\n\n"
        f"Low mood samples ({structured['low_mood_sample_count']}件):\n" + "\n\n".join(structured["low_mood_samples"])
    )
    mini_advice_input = {
        "today_state": today_state,
        "structured_counts": structured["counts"],
        "structured_comparisons": structured["comparisons"],
        "high_mood_samples": structured["high_mood_samples"],
        "low_mood_samples": structured["low_mood_samples"],
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
        "当日の Done/Drop/PFC/Notes/Mood の完成値はまだ存在しない前提です。\n"
        "データ不足の項目は無理に埋めず、使える根拠だけで文章を組み立ててください。\n\n"
        f"今朝の状態:\n{json.dumps(today_state, ensure_ascii=False, indent=2)}\n\n"
        f"過去30日の比較要約:\n{json.dumps(structured['counts'], ensure_ascii=False, indent=2)}\n"
        f"{json.dumps(structured['comparisons'], ensure_ascii=False, indent=2)}\n\n"
        f"mini整理結果:\n{mini_analysis}\n"
    )
    final_advice_input = {
        "today_state": today_state,
        "structured_counts": structured["counts"],
        "structured_comparisons": structured["comparisons"],
        "mini_analysis": mini_analysis,
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
