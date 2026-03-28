from __future__ import annotations

import importlib
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

import requests

from publish.read_daily_log import DailyLogSummary, read_daily_log
from scripts.note_batch_labeler import label_notes_in_batches
from scripts.openai_chat_utils import chat_completion as shared_chat_completion
from scripts.today_advice_feature_builder import build_daily_feature_table
from scripts.today_advice_pattern_analyzer import analyze_exploratory_patterns
from scripts.today_advice_regression import run_low_mood_regression
from scripts.today_advice_lightgbm import run_lightgbm_low_mood
from scripts.today_advice_renderer import build_analysis_json, render_today_advice_from_analysis
from scripts.sleep_utils import resolve_sleep_duration_minutes, resolve_sleep_for_target_date
from scripts.today_advice_audit import (
    TodayAdviceAuditLogger,
    count_missing,
    is_today_advice_debug_enabled,
    safe_json,
    summarize_regression,
)

OPENAI_TIMEOUT = (5, 90)
DEFAULT_MINI_MODEL = "gpt-4.1-mini"
DEFAULT_FINAL_MODEL = "gpt-4.1"
LOOKBACK_DAYS = 30
RECENT_WINDOW_DAYS = 14
SHORT_WINDOW_DAYS = 7
SAMPLE_DAYS_PER_BUCKET = 5


MEAL_NUMERIC_FIELDS = ("kcal", "protein", "fat", "carb")
NOTES_SIGNAL_PATTERNS = {
    "fatigue": ["疲れ", "だる", "しんど", "倦怠", "疲労"],
    "sleep_issue": ["寝不足", "眠い", "眠気", "寝なかった", "寝れてない", "睡眠不足", "夜更かし"],
    "overeating": ["食べすぎ", "食べ過ぎ", "夜食", "食べすぎた", "食欲暴走", "食べ過ぎた"],
    "stress": ["喧嘩", "後悔", "ストレス", "イライラ", "不安", "焦り"],
    "focus": ["集中できた", "集中", "はかど", "捗", "進んだ", "没頭"],
    "exercise": ["ジム", "運動", "ランニング", "筋トレ", "散歩", "ストレッチ"],
    "recovery": ["体調が良い", "回復", "調子が良い", "元気", "持ち直", "楽になった"],
}
LOCATION_PATTERN_KEYS = ("home_heavy_day", "office_heavy_day", "outing_heavy_day", "late_outing_day", "multi_stop_day")
MINI_SYSTEM_PROMPT = """あなたは Today advice 用の判定JSONを作る前段整理アシスタントです。
役割は、当日の sleep 系データと過去実績だけから判断材料を整理し、最終本文の元になる判定JSONだけを作ることです。

必須ルール:
- 出力は必ず JSON オブジェクト 1 個のみ。前置きや補足文は禁止
- 最終本文、見出し、メール文面は絶対に書かない
- Today advice で当日参照してよいのは sleep 系のみ
- today sleep only / non-sleep historical only / must include recent 7-day trend
- 行動・支出・食事・メモ・位置情報系は当日値を使わず、過去実績のみから評価する
- 当日の diary 本文、過去の日記本文、diary由来要約は使わない
- diary 本文 / 過去 diary 本文は使わない
- 日本語の自由記述として使ってよいのは過去履歴に含まれる notes のみ
- location summary は過去履歴の構造化コンテキストとしてのみ参照してよい
- 当日の未入力や未完了は評価対象にしない
- 当日の sleep と、過去7日・14日・30日、および mood 高低日の比較から判断する
- 当日の meal / done / drop / spend / notes / location summary / 記録有無 を根拠に解釈しない
- 因果は断定しない。相関は「傾向」「近さ」「可能性」に留める
- 支出から感情を安易に推測しない
- 当日の食事未記録、メモ未記録、タスク未完了、支出ゼロをネガティブ評価しない
- 「今日はメモがない」「今日はタスク完了ゼロ」「今日は食事記録がない」など当日値ベースの断定は禁止
- recommended_actions は 1 個または 2 個の短い文字列に絞る
- evidence_used には本文生成に使う根拠を簡潔な配列で残す
- evidence_used には sleep 根拠 / recent 7-day behavior 根拠 / good-bad comparison 根拠を残す
- 必ず recent 7-day behavior pattern を 1 つ以上 judgment に残す

必須キー:
- day_type
- main_bottleneck
- priority_theme
- primary_risk
- good_pattern_similarity
- bad_pattern_similarity
- notes_signal
- recording_signal
- meal_signal
- notes_pattern_signal
- location_pattern_signal
- good_bad_behavior_gap
- evidence_used
- recommended_actions
- sleep_signal
- recent_behavior_pattern
- recording_pattern
- priority_action
"""

FINAL_SYSTEM_PROMPT = """あなたは朝メール冒頭に載せる Today advice 本文を書くアシスタントです。
役割は、判定JSONと当日の sleep 系事実、そして過去実績だけを使い、短めでも密度の高い日本語本文を書くことです。

最優先要件:
- 出力は日本語本文のみ。見出し、タイトル、箇条書き、JSONは禁止
- 3〜5文構成、260〜420字程度
- 最初の文は必ずしも睡眠から始めない。強い根拠から先に書く
- 必ず recent 7-day behavior pattern を 1 つ以上本文に入れる
- 2段落以内
- 一般論は禁止
- 事実 → 解釈 → 今日の優先行動 の順に自然につなぐ
- 行動提案は 1〜2 個に絞る
- 同じ事実の言い換えを繰り返さない
- 読後に「今日は何を優先する日か」が明確に残るようにする

入力制約:
- Today advice で当日参照してよいのは sleep 系のみ
- today sleep only / non-sleep historical only / must include recent 7-day trend
- 睡眠は optional。sleep_should_mention=true か、sleep差分・score差分が大きい時のみ触れる
- 行動・支出・食事・メモ・位置情報系は当日値を使わず、過去実績のみから評価する
- 当日の done / drop / spend / meal / notes / location summary を根拠に解釈しない
- 当日の未入力や未完了は評価対象にしない
- 当日の diary 本文、過去の日記本文は使わない
- notes 以外の自由記述を捏造しない
- location summary は過去実績の事実コンテキストとしてのみ参照してよい
- 判定JSONにない論点を勝手に増やしすぎない
- 支出から感情を断定しない
- 食事未記録から健康状態を断定しない
- 「把握が難しい」「低調」「停滞」などを、当日未記録や当日ゼロ件を根拠に断定しない
- Notes品質・parse・unknown率など内部品質事情は本文に出さない

禁止例:
- バランスの良い食事を心がけましょう
- 適度に休憩しましょう
- 無理せず過ごしましょう
- 規則正しい生活を意識しましょう
- メモの記録がなく、気分や課題の把握が難しい
- 今日はタスク完了がゼロで低調
- 支出が少ない/多いので今日は〜
- 食事記録がないため〜

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
        "historical_behavior_pattern_keys": sorted(today_state.get("historical_behavior_patterns", {}).keys()) if isinstance(today_state, Mapping) and isinstance(today_state.get("historical_behavior_patterns"), Mapping) else [],
        "historical_recording_pattern_keys": sorted(today_state.get("historical_recording_patterns", {}).keys()) if isinstance(today_state, Mapping) and isinstance(today_state.get("historical_recording_patterns"), Mapping) else [],
        "historical_context_keys": sorted(today_state.get("historical_context", {}).keys()) if isinstance(today_state, Mapping) and isinstance(today_state.get("historical_context"), Mapping) else [],
        "last_30_days_count": counts.get("last_30_days_count") if isinstance(counts, Mapping) else None,
        "top_good_days_count": counts.get("top_good_days_count") if isinstance(counts, Mapping) else None,
        "top_bad_days_count": counts.get("top_bad_days_count") if isinstance(counts, Mapping) else None,
        "notes_used_count": counts.get("notes_used_count") if isinstance(counts, Mapping) else None,
        "diary_used": False,
        "past_diary_used": False,
        "location_summary_used": True,
        "notes_used": notes_used,
        "recent_7d_summary": structured.get("comparisons", {}).get("recent_7d") if isinstance(structured.get("comparisons", {}), Mapping) else None,
        "recent_14d_summary": structured.get("comparisons", {}).get("recent_14d") if isinstance(structured.get("comparisons", {}), Mapping) else None,
        "recent_30d_summary": structured.get("comparisons", {}).get("recent_30d") if isinstance(structured.get("comparisons", {}), Mapping) else None,
        "good_vs_bad_delta": structured.get("comparisons", {}).get("good_vs_bad_delta") if isinstance(structured.get("comparisons", {}), Mapping) else None,
        "notes_signal_comparison": structured.get("comparisons", {}).get("notes_signal_comparison") if isinstance(structured.get("comparisons", {}), Mapping) else None,
        "meal_mood_comparison": structured.get("comparisons", {}).get("meal_mood_comparison") if isinstance(structured.get("comparisons", {}), Mapping) else None,
        "location_pattern_comparison": structured.get("comparisons", {}).get("location_pattern_comparison") if isinstance(structured.get("comparisons", {}), Mapping) else None,
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
    include_next_day: bool = True,
) -> list[DailyLogSummary]:
    base_day = datetime.strptime(target_date, "%Y-%m-%d")
    summaries: list[DailyLogSummary] = []
    if include_next_day:
        next_day = (base_day + timedelta(days=1)).strftime("%Y-%m-%d")
        next_summary = read_daily_log(
            daily_log_read_url=daily_log_read_url,
            target_date=next_day,
            bearer_token=bearer_token,
        )
        if next_summary:
            summaries.append(next_summary)
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


def _resolve_today_sleep_candidates(*, target_date: str, today_summary: DailyLogSummary, history: Sequence[DailyLogSummary]) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]], str]:
    return resolve_sleep_for_target_date(
        target_date=target_date,
        today_summary=today_summary,
        history_summaries=history,
    )


def _build_metric_snapshot(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    return {
        "sleep_duration_min_avg": _mean([item.sleep_duration_min for item in items]),
        "sleep_score_avg": _mean([item.sleep_score for item in items]),
        "done_count_avg": _mean([_safe_float(item.done_count) for item in items]),
        "drop_count_avg": _mean([_safe_float(item.drop_count) for item in items]),
        "expenses_total_avg": _mean([item.expenses_total for item in items]),
    }


def _compare_metric_windows(current: Optional[float], base: Optional[float]) -> Optional[str]:
    if current is None or base is None:
        return None
    if current > base:
        return "up"
    if current < base:
        return "down"
    return "flat"


def _trend_direction(values: Sequence[Optional[float]]) -> Optional[str]:
    nums = [float(v) for v in values if v is not None]
    if len(nums) < 3:
        return None
    if nums[0] < nums[1] < nums[2]:
        return "up"
    if nums[0] > nums[1] > nums[2]:
        return "down"
    return None
def _meal_metric_avg(items: Sequence[DailyLogSummary], field_name: str) -> Optional[float]:
    return _mean([_safe_float(getattr(item, field_name, None)) for item in items])


def _extract_notes_signals(note: Optional[str]) -> dict[str, bool]:
    text = _safe_text(note)
    if not text:
        return {key: False for key in NOTES_SIGNAL_PATTERNS}
    lowered = text.lower()
    return {key: any(keyword in text or keyword in lowered for keyword in keywords) for key, keywords in NOTES_SIGNAL_PATTERNS.items()}


def _notes_signal_rates(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    if not items:
        return {f"{key}_rate": None for key in NOTES_SIGNAL_PATTERNS}
    counts = {key: 0 for key in NOTES_SIGNAL_PATTERNS}
    for item in items:
        signals = _extract_notes_signals(item.notes)
        for key, matched in signals.items():
            if matched:
                counts[key] += 1
    return {f"{key}_rate": round(counts[key] / len(items), 2) for key in NOTES_SIGNAL_PATTERNS}


def _extract_location_patterns(summary: Optional[str]) -> dict[str, bool]:
    text = _safe_text(summary) or ""
    home = any(keyword in text for keyword in ["自宅", "家", "在宅", "家中心"])
    office = any(keyword in text for keyword in ["オフィス", "出社", "会社", "職場"])
    outing = any(keyword in text for keyword in ["外出", "移動", "外", "買い物", "カフェ", "出かけ"])
    late = any(keyword in text for keyword in ["深夜", "夜遅", "終電", "22:", "23:", "24:", "夜まで"])
    multi = any(keyword in text for keyword in ["→", "→", "巡", "複数", "移動多", "立ち寄", "はしご"]) or text.count("・") >= 2
    return {
        "home_heavy_day": home and not office,
        "office_heavy_day": office,
        "outing_heavy_day": outing,
        "late_outing_day": late,
        "multi_stop_day": multi,
    }


def _location_pattern_rates(items: Sequence[DailyLogSummary]) -> dict[str, Optional[float]]:
    if not items:
        return {f"{key}_rate": None for key in LOCATION_PATTERN_KEYS}
    counts = {key: 0 for key in LOCATION_PATTERN_KEYS}
    for item in items:
        patterns = _extract_location_patterns(item.location_summary)
        for key, matched in patterns.items():
            if matched:
                counts[key] += 1
    return {f"{key}_rate": round(counts[key] / len(items), 2) for key in LOCATION_PATTERN_KEYS}


def _build_behavior_snapshot(items: Sequence[DailyLogSummary]) -> dict[str, Any]:
    snapshot = {
        **_build_metric_snapshot(items),
        "notes_recording_rate": _recording_rate(items, lambda item: item.notes),
        "meal_logged_rate": _recording_rate(items, lambda item: [item.meal_summary] if _safe_text(item.meal_summary) else item.meal_photos),
    }
    for field in MEAL_NUMERIC_FIELDS:
        snapshot[f"{field}_avg"] = _meal_metric_avg(items, field)
    snapshot["notes_signals"] = _notes_signal_rates(items)
    snapshot["location_patterns"] = _location_pattern_rates(items)
    return snapshot


def _delta_map(current: Mapping[str, Optional[float]], base: Mapping[str, Optional[float]]) -> dict[str, Optional[float]]:
    keys = set(current.keys()) | set(base.keys())
    return {key: _delta(current.get(key), base.get(key)) for key in sorted(keys)}




def _build_today_state(today_summary: DailyLogSummary, recent_summaries: Sequence[DailyLogSummary]) -> dict[str, Any]:
    yesterday = recent_summaries[0] if recent_summaries else None
    recent_7 = list(recent_summaries[:SHORT_WINDOW_DAYS])
    recent_14 = list(recent_summaries[:RECENT_WINDOW_DAYS])
    recent_30 = list(recent_summaries[:LOOKBACK_DAYS])
    recent_3_chronological = list(reversed(recent_summaries[:3]))

    recent_7_metrics = _build_metric_snapshot(recent_7)
    recent_14_metrics = _build_metric_snapshot(recent_14)
    recent_30_metrics = _build_metric_snapshot(recent_30)

    today_sleep_duration = resolve_sleep_duration_minutes(
        today_summary.sleep_start,
        today_summary.sleep_end,
        today_summary.sleep_duration_min,
    ).resolved_sleep_duration_min
    today_sleep_score = _safe_float(today_summary.sleep_score)

    return {
        "today_is_morning_incomplete": True,
        "today_sleep": {
            "sleep_analysis_jp": today_summary.sleep_analysis_jp,
            "today_condition_forecast_jp": today_summary.today_condition_forecast_jp,
            "sleep_start": today_summary.sleep_start or "未記録",
            "sleep_end": today_summary.sleep_end or "未記録",
            "sleep_duration_min": today_summary.sleep_duration_min,
            "resolved_sleep_duration_min": today_sleep_duration,
            "sleep_score": today_summary.sleep_score,
            "sleep_heart_rate": today_summary.sleep_heart_rate,
            "deep_duration_min": today_summary.deep_duration_min,
            "rem_duration_min": today_summary.rem_duration_min,
            "readiness_stars": today_summary.readiness_stars,
            "readiness_hrv": today_summary.readiness_hrv,
            "readiness_bpm": today_summary.readiness_bpm,
            "baseline_hrv": today_summary.baseline_hrv,
            "baseline_waking_bpm": today_summary.baseline_waking_bpm,
            "comparisons": {
                "vs_yesterday": {
                    "sleep_duration_min_delta": _delta(today_sleep_duration, _safe_float(yesterday.sleep_duration_min) if yesterday else None),
                    "sleep_score_delta": _delta(today_sleep_score, _safe_float(yesterday.sleep_score) if yesterday else None),
                },
                "vs_recent_7d_avg": {
                    "sleep_duration_min_delta": _delta(today_sleep_duration, recent_7_metrics["sleep_duration_min_avg"]),
                    "sleep_score_delta": _delta(today_sleep_score, recent_7_metrics["sleep_score_avg"]),
                },
                "recent_7d_avg": {
                    "sleep_duration_min_avg": recent_7_metrics["sleep_duration_min_avg"],
                    "sleep_score_avg": recent_7_metrics["sleep_score_avg"],
                },
                "recent_14d_avg": {
                    "sleep_duration_min_avg": recent_14_metrics["sleep_duration_min_avg"],
                    "sleep_score_avg": recent_14_metrics["sleep_score_avg"],
                },
            },
            "recent_3day_trend": {
                "sleep_duration_min": _trend_direction([item.sleep_duration_min for item in recent_3_chronological]),
                "sleep_score": _trend_direction([item.sleep_score for item in recent_3_chronological]),
            },
        },
        "historical_behavior_patterns": {
            "recent_7d_avg": recent_7_metrics,
            "recent_14d_avg": recent_14_metrics,
            "recent_30d_avg": recent_30_metrics,
            "recent_7d_vs_30d": {
                "done_count": _compare_metric_windows(recent_7_metrics["done_count_avg"], recent_30_metrics["done_count_avg"]),
                "drop_count": _compare_metric_windows(recent_7_metrics["drop_count_avg"], recent_30_metrics["drop_count_avg"]),
                "spend_total": _compare_metric_windows(recent_7_metrics["expenses_total_avg"], recent_30_metrics["expenses_total_avg"]),
            },
            "recent_14d_trend": {
                "done_count": _trend_direction([_safe_float(item.done_count) for item in reversed(recent_summaries[:14])]),
                "drop_count": _trend_direction([_safe_float(item.drop_count) for item in reversed(recent_summaries[:14])]),
                "spend_total": _trend_direction([_safe_float(item.expenses_total) for item in reversed(recent_summaries[:14])]),
            },
            "meal_mood_comparison": {
                "recent_7d": {f"{field}_avg": _meal_metric_avg(recent_7, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_14d": {f"{field}_avg": _meal_metric_avg(recent_14, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_30d": {f"{field}_avg": _meal_metric_avg(recent_30, field) for field in MEAL_NUMERIC_FIELDS},
            },
        },
        "historical_recording_patterns": {
            "notes_recording_rate_7d": _recording_rate(recent_7, lambda item: item.notes),
            "notes_recording_rate_14d": _recording_rate(recent_14, lambda item: item.notes),
            "meal_logged_rate_7d": _recording_rate(recent_7, lambda item: [item.meal_summary] if _safe_text(item.meal_summary) else item.meal_photos),
            "meal_logged_rate_14d": _recording_rate(recent_14, lambda item: [item.meal_summary] if _safe_text(item.meal_summary) else item.meal_photos),
            "location_recording_rate_7d": _recording_rate(recent_7, lambda item: item.location_summary),
            "location_recording_rate_14d": _recording_rate(recent_14, lambda item: item.location_summary),
        },
        "historical_context": {
            "recent_7d_location_samples": [item.location_summary for item in recent_7 if _safe_text(item.location_summary)],
            "recent_notes_samples": [item.notes for item in recent_14 if _safe_text(item.notes)][:5],
            "historical_daily_score_avg": _mean([normalize_mood_to_score(item.mood) for item in recent_30]),
            "notes_signal_comparison": {
                "recent_7d": _notes_signal_rates(recent_7),
            },
            "location_pattern_comparison": {
                "recent_7d": _location_pattern_rates(recent_7),
            },
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
        "kcal": summary.kcal,
        "protein": summary.protein,
        "fat": summary.fat,
        "carb": summary.carb,
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
            **_build_behavior_snapshot(items),
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
            "recent_30d": compare(history[:LOOKBACK_DAYS]),
            "high_mood": compare(high),
            "low_mood": compare(low),
            "middle_mood": compare(middle),
            "meal_mood_comparison": {
                "high_mood": {f"{field}_avg": _meal_metric_avg(high, field) for field in MEAL_NUMERIC_FIELDS},
                "low_mood": {f"{field}_avg": _meal_metric_avg(low, field) for field in MEAL_NUMERIC_FIELDS},
                "middle_mood": {f"{field}_avg": _meal_metric_avg(middle, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_7d": {f"{field}_avg": _meal_metric_avg(recent_7, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_14d": {f"{field}_avg": _meal_metric_avg(recent_14, field) for field in MEAL_NUMERIC_FIELDS},
                "recent_30d": {f"{field}_avg": _meal_metric_avg(history[:LOOKBACK_DAYS], field) for field in MEAL_NUMERIC_FIELDS},
                "good_vs_bad_delta": {
                    "kcal": _delta(_meal_metric_avg(top_good_days, "kcal"), _meal_metric_avg(top_bad_days, "kcal")),
                    "protein": _delta(_meal_metric_avg(top_good_days, "protein"), _meal_metric_avg(top_bad_days, "protein")),
                    "fat": _delta(_meal_metric_avg(top_good_days, "fat"), _meal_metric_avg(top_bad_days, "fat")),
                    "carb": _delta(_meal_metric_avg(top_good_days, "carb"), _meal_metric_avg(top_bad_days, "carb")),
                },
            },
            "notes_signal_comparison": {
                "high_mood": _notes_signal_rates(high),
                "low_mood": _notes_signal_rates(low),
                "recent_7d": _notes_signal_rates(recent_7),
                "good_vs_bad_delta": _delta_map(_notes_signal_rates(top_good_days), _notes_signal_rates(top_bad_days)),
            },
            "location_pattern_comparison": {
                "high_mood": _location_pattern_rates(high),
                "low_mood": _location_pattern_rates(low),
                "recent_7d": _location_pattern_rates(recent_7),
                "good_vs_bad_delta": _delta_map(_location_pattern_rates(top_good_days), _location_pattern_rates(top_bad_days)),
            },
            "good_vs_bad_delta": {
                "sleep_duration_min": _delta(_build_metric_snapshot(top_good_days)["sleep_duration_min_avg"], _build_metric_snapshot(top_bad_days)["sleep_duration_min_avg"]),
                "sleep_score": _delta(_build_metric_snapshot(top_good_days)["sleep_score_avg"], _build_metric_snapshot(top_bad_days)["sleep_score_avg"]),
                "done_count": _delta(_build_metric_snapshot(top_good_days)["done_count_avg"], _build_metric_snapshot(top_bad_days)["done_count_avg"]),
                "drop_count": _delta(_build_metric_snapshot(top_good_days)["drop_count_avg"], _build_metric_snapshot(top_bad_days)["drop_count_avg"]),
                "spend_total": _delta(_build_metric_snapshot(top_good_days)["expenses_total_avg"], _build_metric_snapshot(top_bad_days)["expenses_total_avg"]),
                "kcal": _delta(_meal_metric_avg(top_good_days, "kcal"), _meal_metric_avg(top_bad_days, "kcal")),
                "protein": _delta(_meal_metric_avg(top_good_days, "protein"), _meal_metric_avg(top_bad_days, "protein")),
                "fat": _delta(_meal_metric_avg(top_good_days, "fat"), _meal_metric_avg(top_bad_days, "fat")),
                "carb": _delta(_meal_metric_avg(top_good_days, "carb"), _meal_metric_avg(top_bad_days, "carb")),
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


def build_today_advice_generation_context(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: str,
) -> Optional[dict[str, Any]]:
    history = load_daily_logs_for_period(
        daily_log_read_url=daily_log_read_url,
        bearer_token=bearer_token,
        target_date=target_date,
        days=LOOKBACK_DAYS,
    )
    if not history:
        return None

    today_summary = next((item for item in history if item.target_date == target_date), history[0])
    historical_summaries = [item for item in history if item.target_date != target_date and item.target_date < target_date]
    sleep_candidates, selected_sleep_candidate, sleep_properties_source = _resolve_today_sleep_candidates(
        target_date=target_date,
        today_summary=today_summary,
        history=history,
    )
    structured = _build_structured_comparison(historical_summaries)
    recent_prior_days = historical_summaries[:RECENT_WINDOW_DAYS]
    today_state = _build_today_state(today_summary, recent_prior_days)
    if selected_sleep_candidate:
        today_state["today_sleep"]["sleep_duration_min"] = selected_sleep_candidate.get("resolved_sleep_duration_min")
        today_state["today_sleep"]["sleep_score"] = selected_sleep_candidate.get("sleep_score")
        today_state["today_sleep"]["sleep_start"] = selected_sleep_candidate.get("sleep_start") or "未記録"
        today_state["today_sleep"]["sleep_end"] = selected_sleep_candidate.get("sleep_end") or "未記録"
        today_state["today_sleep"]["sleep_available"] = True
        today_state["today_sleep"]["duration_source"] = selected_sleep_candidate.get("duration_source")
    else:
        today_state["today_sleep"]["sleep_available"] = False
        today_state["today_sleep"]["duration_source"] = "missing"
    today_sleep_context = {
        "sleep_available": bool(today_state["today_sleep"].get("sleep_available")),
        "sleep_invalid_reason": None if selected_sleep_candidate else "missing_sleep_signal",
        "sleep_hours": (
            round(float(selected_sleep_candidate.get("resolved_sleep_duration_min", 0)) / 60.0, 2)
            if selected_sleep_candidate and selected_sleep_candidate.get("resolved_sleep_duration_min") is not None
            else None
        ),
        "duration_source": (selected_sleep_candidate or {}).get("duration_source") or "missing",
        "bedtime": ((selected_sleep_candidate or {}).get("sleep_start") or "")[11:16] or None,
        "sleep_score": (selected_sleep_candidate or {}).get("sleep_score"),
    }
    notes_used = any(_safe_text(item.notes) for item in historical_summaries)
    judgment_input = {
        "today_sleep": today_state["today_sleep"],
        "historical_behavior_patterns": today_state["historical_behavior_patterns"],
        "historical_recording_patterns": today_state["historical_recording_patterns"],
        "historical_context": today_state["historical_context"],
        "structured_historical_comparison": {
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
    return {
        "history": history,
        "historical_summaries": historical_summaries,
        "today_summary": today_summary,
        "structured": structured,
        "today_state": today_state,
        "notes_used": notes_used,
        "judgment_input": judgment_input,
        "sleep_candidates": sleep_candidates,
        "selected_sleep_candidate": selected_sleep_candidate,
        "sleep_properties_source": sleep_properties_source,
        "today_sleep_context": today_sleep_context,
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
    return shared_chat_completion(model=model, system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.3)


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
        "sleep_signal",
        "recent_behavior_pattern",
        "recording_pattern",
        "priority_action",
        "meal_signal",
        "notes_pattern_signal",
        "location_pattern_signal",
        "good_bad_behavior_gap",
    ):
        value = normalized.get(key)
        normalized[key] = "" if value is None else str(value).strip()
    return normalized


def build_judgment_user_prompt(*, judgment_input: Mapping[str, Any], structured: Mapping[str, Any]) -> str:
    return f"""以下の材料から、Today advice の本文を書く前段として判定JSONだけを返してください。
出力は JSON オブジェクト 1 個のみで、本文・見出し・説明は禁止です。
recommended_actions は 1〜2 個、evidence_used は本文生成に使う根拠だけを短く列挙してください。
必須:
- today sleep only
- non-sleep historical only
- must include recent 7-day trend
- 必ず recent 7-day behavior pattern を 1 つ以上 judgment に残す
- evidence_used には sleep 根拠 / recent 7-day behavior 根拠 / good-bad comparison 根拠の3系統を含める
制約:
- 当日データとして参照するのは sleep 系のみ
- それ以外は historical data only
- 当日の done / drop / spend / meal / notes / location summary を根拠に解釈しない
- 当日未入力や未完了をネガティブ評価しない

A. 今日の sleep
{json.dumps(judgment_input["today_sleep"], ensure_ascii=False, indent=2)}

B. 過去の行動パターン
{json.dumps(judgment_input["historical_behavior_patterns"], ensure_ascii=False, indent=2)}

C. 過去の記録パターン
{json.dumps(judgment_input["historical_recording_patterns"], ensure_ascii=False, indent=2)}

D. 過去コンテキスト
{json.dumps(judgment_input["historical_context"], ensure_ascii=False, indent=2)}

E. 過去30日比較
{json.dumps(structured["comparisons"], ensure_ascii=False, indent=2)}

F. 過去30日の集計サマリ
{json.dumps(structured["last_30_days_summary"], ensure_ascii=False, indent=2)}

G. 良い日サンプル
{json.dumps(structured["top_good_days"], ensure_ascii=False, indent=2)}

H. 悪い日サンプル
{json.dumps(structured["top_bad_days"], ensure_ascii=False, indent=2)}"""


def build_final_user_prompt(*, judgment_json: Mapping[str, Any], today_facts: Mapping[str, Any]) -> str:
    return f"""以下の判定JSONと当日の最小限の事実だけを使って、Today advice の本文を書いてください。
出力は見出しなしの日本語本文のみ、2段落以内、220〜380字程度です。
事実 → 解釈 → 今日の優先行動 の順で、行動提案は recommended_actions にある 1〜2 個へ絞ってください。
構成は次の3要素を自然文で必ず含めてください。
1. 今日の睡眠状態から見たコンディション
2. 直近7日間の行動・記録傾向
3. 今日まず取るべき具体行動
必須:
- today sleep only
- non-sleep historical only
- must include recent 7-day trend
- 必ず recent 7-day behavior pattern を 1 つ以上本文に入れる
制約:
- 当日データとして参照するのは sleep 系のみ
- それ以外は historical data only
- 当日の done / drop / spend / meal / notes / location summary を根拠に解釈しない
- 当日未入力や未完了を根拠に「把握が難しい」「低調」「停滞」などと断定しない

判定JSON:
{json.dumps(judgment_json, ensure_ascii=False, indent=2)}

当日の事実:
{json.dumps(today_facts, ensure_ascii=False, indent=2)}"""


def generate_today_advice(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: str,
) -> Optional[MoodAdviceResult]:
    context = build_today_advice_generation_context(
        daily_log_read_url=daily_log_read_url,
        bearer_token=bearer_token,
        target_date=target_date,
    )
    if not context:
        logging.info("Skipping Today advice because no Daily Log history is available. target_date=%s", target_date)
        return None

    history = context["history"]
    structured = context["structured"]
    today_summary = context["today_summary"]
    historical_summaries = context["historical_summaries"]
    sleep_candidates = list(context.get("sleep_candidates") or [])
    selected_sleep_candidate = context.get("selected_sleep_candidate")
    sleep_properties_source = context.get("sleep_properties_source")
    today_sleep_context = dict(context.get("today_sleep_context") or {})
    debug_enabled = is_today_advice_debug_enabled()
    audit = TodayAdviceAuditLogger(target_date=target_date, debug=debug_enabled)
    final_model = os.getenv("TODAY_ADVICE_FINAL_MODEL", os.getenv("OPENAI_MODEL", DEFAULT_FINAL_MODEL)).strip() or DEFAULT_FINAL_MODEL
    mini_model = os.getenv("TODAY_ADVICE_MINI_MODEL", DEFAULT_MINI_MODEL).strip() or DEFAULT_MINI_MODEL

    fetched_count = len(history)
    usable_count = len(historical_summaries)
    skipped_count = max(0, fetched_count - usable_count)
    analysis_end_date = target_date
    analysis_start_date = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    fetch_payload = {
        "window_days": LOOKBACK_DAYS,
        "analysis_start_date": analysis_start_date,
        "analysis_end_date": analysis_end_date,
        "fetched_count": fetched_count,
        "usable_rows_count": usable_count,
        "skipped_rows_count": skipped_count,
        "missing": {
            "mood": sum(count_missing(item.mood) for item in historical_summaries),
            "sleep_hours": sum(1 for item in historical_summaries if item.sleep_duration_min is None),
            "sleep_score": sum(count_missing(item.sleep_score) for item in historical_summaries),
            "notes": sum(count_missing(item.notes) for item in historical_summaries),
            "task_done": sum(count_missing(item.done_count) for item in historical_summaries),
            "task_drop": sum(count_missing(item.drop_count) for item in historical_summaries),
            "spending": sum(count_missing(item.expenses_total) for item in historical_summaries),
        },
    }
    audit.put("fetch", fetch_payload)
    audit.info(
        "[TodayAdvice][Fetch] target_date=%s window=%s fetched=%s usable=%s skipped=%s",
        target_date,
        LOOKBACK_DAYS,
        fetched_count,
        usable_count,
        skipped_count,
    )
    audit.info(
        "[TodayAdvice][Fetch] missing: mood=%s sleep_hours=%s sleep_score=%s notes=%s done=%s drop=%s spending=%s",
        fetch_payload["missing"]["mood"],
        fetch_payload["missing"]["sleep_hours"],
        fetch_payload["missing"]["sleep_score"],
        fetch_payload["missing"]["notes"],
        fetch_payload["missing"]["task_done"],
        fetch_payload["missing"]["task_drop"],
        fetch_payload["missing"]["spending"],
    )
    audit.put(
        "sleep_resolve",
        {
            "target_date": target_date,
            "rule": "target_date = date((sleep_start or sleep_end) in JST - 5h)",
            "used_saved_sleep_properties": sleep_properties_source == "saved_today_properties",
            "sleep_properties_source": sleep_properties_source,
            "candidates": sleep_candidates,
            "selected": selected_sleep_candidate,
        },
    )
    audit.info("[Sleep] sleep_candidates=%s", safe_json(sleep_candidates))
    audit.info("[Sleep] selected_sleep_candidate=%s", safe_json(selected_sleep_candidate) if selected_sleep_candidate else "{}")
    audit.info(
        "[TodayAdvice][SleepResolve] target_date=%s rule=05:00JST start_or_end_minus_5h used_saved_sleep_properties=%s source=%s",
        target_date,
        sleep_properties_source == "saved_today_properties",
        sleep_properties_source,
    )
    for idx, candidate in enumerate(sleep_candidates):
        audit.info(
            "[TodayAdvice][SleepCandidates] idx=%s candidate_date=%s sleep_start=%s sleep_end=%s raw_sleep_duration_min=%s resolved_sleep_duration_min=%s sleep_score=%s candidate_valid_flag=%s invalid_reason=%s selection_reason=%s candidate_target_date=%s duration_source=%s",
            idx,
            candidate.get("candidate_date"),
            candidate.get("sleep_start"),
            candidate.get("sleep_end"),
            candidate.get("raw_sleep_duration_min"),
            candidate.get("resolved_sleep_duration_min"),
            candidate.get("sleep_score"),
            candidate.get("candidate_valid_flag"),
            candidate.get("invalid_reason"),
            candidate.get("selection_reason"),
            candidate.get("candidate_target_date"),
            candidate.get("duration_source"),
        )
    audit.info(
        "[TodayAdvice][SleepSelected] selected=%s",
        safe_json(selected_sleep_candidate) if selected_sleep_candidate else "{}",
    )
    audit.info("[TodayAdvice][SleepSelected] selected_candidate_source=%s", sleep_properties_source)
    try:
        note_label_audit: dict[str, Any] = {}
        notes_debug_dir = os.path.join(
            tempfile.gettempdir(),
            "today_advice_notes_raw",
            f"{target_date}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        )
        note_labels = label_notes_in_batches(
            summaries=historical_summaries,
            chat_completion=_chat_completion,
            model=mini_model,
            raw_response_dir=notes_debug_dir,
            audit=note_label_audit,
        )
        notes_total_count = len(historical_summaries)
        notes_non_empty_count = sum(1 for item in historical_summaries if _safe_text(item.notes))
        notes_labeled = list(note_labels.values())
        notes_parse_success_rate = float(note_label_audit.get("notes_parse_success_rate", 0.0) or 0.0)
        notes_unknown_rate = float(note_label_audit.get("unknown_rate", note_label_audit.get("notes_unknown_rate", 0.0)) or 0.0)
        notes_signals_detected_count = int(note_label_audit.get("signals_detected_count", 0) or 0)
        notes_payload = {
            "total_count": notes_total_count,
            "non_empty_count": notes_non_empty_count,
            "api_calls": note_label_audit.get("api_calls", 0),
            "labeled_count": len(notes_labeled),
            "fallback_unknown_count": sum(1 for item in notes_labeled if item.confidence == "low" and item.sentiment_label == "unknown"),
            "dataframe_sentiment_counts": {
                "positive": sum(1 for item in notes_labeled if item.sentiment_label == "positive"),
                "neutral": sum(1 for item in notes_labeled if item.sentiment_label == "neutral"),
                "negative": sum(1 for item in notes_labeled if item.sentiment_label == "negative"),
            },
            "dataframe_flag_counts": {
                "fatigue": sum(1 for item in notes_labeled if item.fatigue_flag),
                "stress": sum(1 for item in notes_labeled if item.stress_flag),
                "social_load": sum(1 for item in notes_labeled if item.social_load_flag),
                "achievement": sum(1 for item in notes_labeled if item.achievement_flag),
                "self_care": sum(1 for item in notes_labeled if item.self_care_flag),
                "sleep_issue": sum(1 for item in notes_labeled if item.sleep_issue_flag),
            },
            "raw_sentiment_counts": note_label_audit.get("raw_sentiment_counts", {}),
            "raw_flag_counts": note_label_audit.get("raw_flag_counts", {}),
            "normalized_sentiment_counts": note_label_audit.get("normalized_sentiment_counts", {}),
            "normalized_flag_counts": note_label_audit.get("normalized_flag_counts", {}),
            "top_evidence_keywords": [k for k, _v in sorted({kw: sum(kw in item.evidence_keywords for item in notes_labeled) for item in notes_labeled for kw in item.evidence_keywords}.items(), key=lambda p: p[1], reverse=True)[:5]],
            "fallback_reason_counts": note_label_audit.get("fallback_reason_counts", {}),
            "raw_response_paths": note_label_audit.get("raw_response_paths", []),
            "notes_classifier_success_rate": note_label_audit.get("notes_classifier_success_rate", 0.0),
            "notes_parse_success_rate": notes_parse_success_rate,
            "unknown_rate": notes_unknown_rate,
            "tag_extract_failed_count": note_label_audit.get("tag_extract_failed_count", 0),
            "parse_low_confidence_count": note_label_audit.get("parse_low_confidence_count", 0),
            "top_tags": note_label_audit.get("top_tags", []),
            "matched_dates_count": note_label_audit.get("matched_dates_count", 0),
            "matched_dates": note_label_audit.get("matched_dates", []),
            "unmatched_input_dates": note_label_audit.get("unmatched_input_dates", []),
            "unmatched_response_dates": note_label_audit.get("unmatched_response_dates", []),
            "signals_detected_count": notes_signals_detected_count,
            "notes_label_quality_low": bool(notes_parse_success_rate < 0.5),
        }
        missing_ids = list(note_label_audit.get("missing_ids") or [])
        missing_ids_detail = [
            {"id": item, "affected_input_dates": [row.target_date for row in historical_summaries if str(item).endswith(row.target_date)]}
            for item in missing_ids
        ]
        exclusion_reasons: list[str] = []
        if notes_payload["notes_parse_success_rate"] < 0.8:
            exclusion_reasons.append("parse_success_rate_low")
        if notes_payload["unknown_rate"] > 0.4:
            exclusion_reasons.append("unknown_rate_high")
        if note_label_audit.get("date_match_failure_count", 0) > 0:
            exclusion_reasons.append("date_match_failure")
        if note_label_audit.get("duplicate_ids"):
            exclusion_reasons.append("duplicate_ids")
        if missing_ids:
            exclusion_reasons.append("missing_ids")
        if note_label_audit.get("unknown_ids"):
            exclusion_reasons.append("unknown_ids")
        quality_high_enough = (
            notes_payload["notes_parse_success_rate"] >= 0.8
            and note_label_audit.get("date_match_failure_count", 0) == 0
            and notes_payload["unknown_rate"] <= 0.4
        )
        if quality_high_enough:
            exclusion_reasons = [reason for reason in exclusion_reasons if reason != "missing_ids"]
        notes_quality_used = "low" if exclusion_reasons else "high"
        notes_payload["notes_quality_used"] = notes_quality_used
        notes_payload["exclusion_reason"] = exclusion_reasons
        notes_payload["missing_ids_detail"] = missing_ids_detail
        audit.put("notes_labeling", notes_payload)
        audit.info(
            "[TodayAdvice][Notes] total=%s non_empty=%s api_calls=%s labeled=%s fallback_unknown=%s",
            notes_payload["total_count"], notes_payload["non_empty_count"], notes_payload["api_calls"], notes_payload["labeled_count"], notes_payload["fallback_unknown_count"],
        )
        audit.info(
            "[TodayAdvice][Notes] raw_sentiment=%s normalized_sentiment=%s dataframe_sentiment=%s",
            safe_json(notes_payload["raw_sentiment_counts"]),
            safe_json(notes_payload["normalized_sentiment_counts"]),
            safe_json(notes_payload["dataframe_sentiment_counts"]),
        )
        audit.info(
            "[TodayAdvice][Notes] raw_flags=%s normalized_flags=%s dataframe_flags=%s",
            safe_json(notes_payload["raw_flag_counts"]),
            safe_json(notes_payload["normalized_flag_counts"]),
            safe_json(notes_payload["dataframe_flag_counts"]),
        )
        reason_counts = notes_payload["fallback_reason_counts"]
        audit.info(
            "[TodayAdvice][Notes] fallback_reasons: parse_error=%s schema_mismatch=%s date_match_failure=%s empty_response=%s",
            reason_counts.get("parse_error_count", 0),
            reason_counts.get("schema_mismatch_count", 0),
            reason_counts.get("date_match_failure_count", 0),
            reason_counts.get("empty_response_count", 0),
        )
        audit.info(
            "[TodayAdvice][Notes] classifier_success_rate=%s parse_success_rate=%s unknown_rate=%s tag_extract_failed=%s parse_low_confidence=%s",
            notes_payload["notes_classifier_success_rate"],
            notes_payload["notes_parse_success_rate"],
            notes_payload["unknown_rate"],
            notes_payload["tag_extract_failed_count"],
            notes_payload["parse_low_confidence_count"],
        )
        audit.info(
            "[Notes] notes_quality_used=%s exclusion_reason=%s missing_ids_detail=%s matched_dates_count=%s unmatched_input_dates=%s unmatched_response_dates=%s parse_success_rate=%s unknown_rate=%s notes_based_features_included_count=%s notes_based_features_excluded_count=%s",
            notes_quality_used,
            safe_json(exclusion_reasons),
            safe_json(missing_ids_detail),
            notes_payload["matched_dates_count"],
            safe_json(notes_payload["unmatched_input_dates"]),
            safe_json(notes_payload["unmatched_response_dates"]),
            notes_payload["notes_parse_success_rate"],
            notes_payload["unknown_rate"],
            0 if notes_quality_used == "low" else len(note_labels),
            len(note_labels) if notes_quality_used == "low" else 0,
        )
        if notes_quality_used == "low":
            note_labels = {d: type(label)(**{**label.__dict__, "sentiment_label": "unknown", "sentiment_score": 0, "fatigue_flag": False, "stress_flag": False, "social_load_flag": False, "achievement_flag": False, "self_care_flag": False, "sleep_issue_flag": False, "signals": [], "derived_flags": {}, "confidence": "low", "parse_quality": "low", "no_signal_note": True, "tag_extract_failed": True, "parse_low_confidence": True}) for d, label in note_labels.items()}
            audit.info("[Notes] excluded_from_today_advice=true")
        audit.info(
            "[TodayAdvice][Notes] top_tags=%s matched_dates_count=%s",
            safe_json(notes_payload["top_tags"]),
            notes_payload["matched_dates_count"],
        )
        audit.info(
            "[TodayAdvice][Notes] unmatched_input_dates=%s unmatched_response_dates=%s",
            safe_json(note_label_audit.get("unmatched_input_dates", [])),
            safe_json(note_label_audit.get("unmatched_response_dates", [])),
        )
        audit.info("[TodayAdvice][Notes] raw_response_paths=%s", safe_json(notes_payload["raw_response_paths"]))
        if notes_payload["raw_response_paths"]:
            audit.info("[TodayAdvice][Notes] raw_responses_saved=%s", safe_json(notes_payload["raw_response_paths"]))
        if notes_non_empty_count > 0 and notes_signals_detected_count == 0:
            logging.warning("notes_label_quality_warning target_date=%s reason=non_empty_but_no_signals", target_date)
        if notes_non_empty_count > 0 and notes_unknown_rate >= 0.9:
            logging.warning("notes_label_quality_warning target_date=%s reason=unknown_rate_high unknown_rate=%.3f", target_date, notes_unknown_rate)
        if not any(notes_payload["dataframe_flag_counts"].values()):
            logging.warning("notes_label_quality_warning target_date=%s reason=dataframe_flags_all_zero", target_date)
        if not notes_payload["top_tags"]:
            logging.warning("notes_label_quality_warning target_date=%s reason=top_tags_empty", target_date)

        feature_df = build_daily_feature_table(historical_summaries, note_labels)
        history_by_date = {item.target_date: item for item in historical_summaries}
        sleep_conversion_samples = []
        for _, row in feature_df.sort_values("date").tail(10).iterrows():
            date = str(row.get("date"))
            raw_item = history_by_date.get(date)
            raw_sleep_duration_min = raw_item.sleep_duration_min if raw_item is not None else None
            raw_sleep_score = raw_item.sleep_score if raw_item is not None else None
            derived_sleep_hours = row.get("sleep_hours")
            sleep_conversion_samples.append(
                {
                    "date": date,
                    "raw_sleep_duration_min": raw_sleep_duration_min,
                    "derived_sleep_hours": round(float(derived_sleep_hours), 2) if derived_sleep_hours is not None and not math.isnan(float(derived_sleep_hours)) else None,
                    "raw_sleep_score": raw_sleep_score,
                    "source_field_name_duration": "sleep_duration_min",
                    "source_field_name_score": "sleep_score",
                    "sleep_valid_flag": bool(row.get("sleep_valid_flag", False)),
                    "sleep_invalid_reason": row.get("sleep_invalid_reason"),
                    "sleep_hours_missing_flag": bool(raw_sleep_duration_min is None),
                    "sleep_score_missing_flag": bool(raw_sleep_score is None),
                }
            )
        feature_payload = {
            "row_count": int(len(feature_df.index)) if not feature_df.empty else 0,
            "column_count": int(len(feature_df.columns)) if not feature_df.empty else 0,
            "created_columns": [str(col) for col in feature_df.columns],
            "sleep_valid_count": int(feature_df["sleep_valid_flag"].fillna(False).sum()) if "sleep_valid_flag" in feature_df else 0,
            "sleep_invalid_count": int((~feature_df["sleep_valid_flag"].fillna(False)).sum()) if "sleep_valid_flag" in feature_df else 0,
            "invalid_reason_counts": feature_df["sleep_invalid_reason"].fillna("unknown").value_counts().to_dict() if "sleep_invalid_reason" in feature_df else {},
            "flag_counts": {
                "sleep_lt_6h_flag_count": int(feature_df["sleep_lt_6h_flag"].fillna(False).sum()) if "sleep_lt_6h_flag" in feature_df else 0,
                "bedtime_after_0100_flag_count": int(feature_df["bedtime_after_0100_flag"].fillna(False).sum()) if "bedtime_after_0100_flag" in feature_df else 0,
                "notes_negative_flag_count": int((feature_df["notes_sentiment_label"].fillna("") == "negative").sum()) if "notes_sentiment_label" in feature_df else 0,
                "notes_fatigue_flag_count": int(feature_df["notes_fatigue_flag"].fillna(False).sum()) if "notes_fatigue_flag" in feature_df else 0,
                "spending_high_flag_count": int(feature_df["spending_high_flag"].fillna(False).sum()) if "spending_high_flag" in feature_df else 0,
                "drop_high_flag_count": int(feature_df["drop_high_flag"].fillna(False).sum()) if "drop_high_flag" in feature_df else 0,
            },
            "numeric_summary": {
                "sleep_hours_mean": round(float(feature_df["sleep_hours"].fillna(0).mean()), 2) if "sleep_hours" in feature_df else 0,
                "sleep_hours_min": round(float(feature_df["sleep_hours"].fillna(0).min()), 2) if "sleep_hours" in feature_df else 0,
                "sleep_hours_max": round(float(feature_df["sleep_hours"].fillna(0).max()), 2) if "sleep_hours" in feature_df else 0,
                "sleep_score_mean": round(float(feature_df["sleep_score"].fillna(0).mean()), 2) if "sleep_score" in feature_df else 0,
                "spending_total_mean": round(float(feature_df["spending_total"].fillna(0).mean()), 2) if "spending_total" in feature_df else 0,
                "task_done_mean": round(float(feature_df["task_done_count"].fillna(0).mean()), 2) if "task_done_count" in feature_df else 0,
                "task_drop_mean": round(float(feature_df["task_drop_count"].fillna(0).mean()), 2) if "task_drop_count" in feature_df else 0,
                "sleep_hours_non_null_count": int(feature_df["sleep_hours"].notna().sum()) if "sleep_hours" in feature_df else 0,
                "sleep_score_non_null_count": int(feature_df["sleep_score"].notna().sum()) if "sleep_score" in feature_df else 0,
            },
            "sleep_feature_conversion_samples": sleep_conversion_samples,
        }
        audit.put("features", feature_payload)
        audit.info("[TodayAdvice][Features] rows=%s cols=%s", feature_payload["row_count"], feature_payload["column_count"])
        audit.info(
            "[TodayAdvice][Features] flags: sleep_lt_6h=%s bedtime_after_0100=%s notes_negative=%s notes_fatigue=%s",
            feature_payload["flag_counts"]["sleep_lt_6h_flag_count"],
            feature_payload["flag_counts"]["bedtime_after_0100_flag_count"],
            feature_payload["flag_counts"]["notes_negative_flag_count"],
            feature_payload["flag_counts"]["notes_fatigue_flag_count"],
        )
        audit.info(
            "[TodayAdvice][Features] numeric: sleep_hours_mean=%s sleep_score_mean=%s task_done_mean=%s task_drop_mean=%s",
            feature_payload["numeric_summary"]["sleep_hours_mean"],
            feature_payload["numeric_summary"]["sleep_score_mean"],
            feature_payload["numeric_summary"]["task_done_mean"],
            feature_payload["numeric_summary"]["task_drop_mean"],
        )
        audit.info(
            "[TodayAdvice][Features] counts: sleep_hours_non_null=%s sleep_score_non_null=%s",
            feature_payload["numeric_summary"]["sleep_hours_non_null_count"],
            feature_payload["numeric_summary"]["sleep_score_non_null_count"],
        )
        audit.info("[TodayAdvice][Features] sleep_conversion_samples=%s", safe_json(sleep_conversion_samples))

        exploratory_summary = analyze_exploratory_patterns(feature_df)
        audit.put("exploratory_analysis", {
            "exploratory_feature_count": len(feature_df.columns),
            "exploratory_target_name": exploratory_summary.get("exploratory_target_name"),
            "top_single_features_for_low_mood": exploratory_summary.get("top_single_features_for_low_mood", []),
            "top_protective_features": exploratory_summary.get("top_protective_features", []),
            "top_combination_patterns_for_low_mood": exploratory_summary.get("top_combination_patterns_for_low_mood", []),
            "top_combination_patterns_for_high_mood": exploratory_summary.get("top_combination_patterns_for_high_mood", []),
        })

        regression_summary = run_low_mood_regression(feature_df)
        regression_payload = summarize_regression(regression_summary)
        audit.put("regression", regression_payload)
        if regression_payload["available"]:
            audit.info(
                "[TodayAdvice][Regression] available=true sample=%s target=%s",
                regression_payload["sample_size"],
                regression_payload["regression_target_name"],
            )
            audit.info("[TodayAdvice][Regression] top_positive=%s", safe_json(regression_payload["top_positive_features"]))
            audit.info("[TodayAdvice][Regression] top_negative=%s", safe_json(regression_payload["top_negative_features"]))
        else:
            audit.info(
                "[TodayAdvice][Regression] available=false reason=%s",
                regression_payload.get("skipped_reason") or "unknown",
            )
        lightgbm_summary = run_lightgbm_low_mood(feature_df)
        audit.put("lightgbm", lightgbm_summary)
        audit.info(
            "[TodayAdvice][LightGBM] available=%s sample=%s reason=%s skipped_columns=%s feature_columns=%s",
            lightgbm_summary.get("available"),
            lightgbm_summary.get("sample_size"),
            lightgbm_summary.get("skipped_reason"),
            safe_json(lightgbm_summary.get("skipped_columns", [])),
            safe_json(lightgbm_summary.get("feature_columns", [])),
        )

        analysis_json = build_analysis_json(
            target_date=target_date,
            today_summary=today_summary,
            features_df=feature_df,
            exploratory_summary=exploratory_summary,
            regression_summary=regression_summary,
            lightgbm_summary=lightgbm_summary,
            notes_label_quality={
                "raw_sentiment_counts": notes_payload.get("raw_sentiment_counts", {}),
                "raw_flag_counts": notes_payload.get("raw_flag_counts", {}),
                "normalized_sentiment_counts": notes_payload.get("normalized_sentiment_counts", {}),
                "normalized_flag_counts": notes_payload.get("normalized_flag_counts", {}),
                "dataframe_sentiment_counts": notes_payload.get("dataframe_sentiment_counts", {}),
                "dataframe_flag_counts": notes_payload.get("dataframe_flag_counts", {}),
                "top_keywords": notes_payload.get("top_evidence_keywords", []),
                "notes_parse_success_rate": notes_payload.get("notes_parse_success_rate", 0.0),
                "unknown_rate": notes_payload.get("unknown_rate", 0.0),
                "top_tags": notes_payload.get("top_tags", []),
                "label_quality_low": notes_payload.get("notes_label_quality_low", False),
            },
            today_sleep_context=today_sleep_context,
        )
        audit.info("[Sleep] renderer_received_sleep_context=%s", safe_json(today_sleep_context))
        today_sleep_hours = analysis_json.get("today_sleep_context", {}).get("sleep_hours")
        today_bedtime = (today_summary.sleep_start or "")[11:16] if today_summary.sleep_start else None
        today_sleep_score = analysis_json.get("today_sleep_context", {}).get("sleep_score")
        today_condition_flags = {
            "prev_sleep_lt_6h": bool(today_sleep_hours is not None and today_sleep_hours < 6),
            "prev_bedtime_after_0100": bool(today_bedtime is not None and today_bedtime >= "01:00"),
            "prev_sleep_score_low": bool(today_sleep_score is not None and today_sleep_score < 70),
        }
        matched_pattern_ids = [",".join(item.get("features", [])) for item in exploratory_summary.get("matched_today_conditions", [])]
        today_match_payload = {
            "today_sleep_context": {
                "sleep_available": analysis_json.get("today_sleep_context", {}).get("sleep_available"),
                "sleep_invalid_reason": analysis_json.get("today_sleep_context", {}).get("sleep_invalid_reason"),
                "sleep_hours": today_sleep_hours,
                "bedtime": today_bedtime,
                "sleep_score": today_sleep_score,
            },
            "match_conditions": today_condition_flags,
            "matched_patterns_count": len(matched_pattern_ids),
            "matched_pattern_ids": matched_pattern_ids,
            "risk_level": analysis_json.get("risk_level"),
            "primary_focus": analysis_json.get("primary_focus"),
            "evidence_used": analysis_json.get("evidence_used", []),
            "reason_codes": analysis_json.get("reason_codes", []),
        }
        audit.put("today_match", today_match_payload)
        final_sleep_context = analysis_json.get("today_sleep_context", {})
        audit.put("today_sleep_context", final_sleep_context)
        audit.info("[Sleep] final_today_sleep_context=%s", safe_json(final_sleep_context))
        audit.info(
            "[TodayAdvice][SleepAvailability] sleep_available=%s reason=%s duration_source=%s selected_candidate_date=%s",
            final_sleep_context.get("sleep_available"),
            final_sleep_context.get("sleep_invalid_reason") or (selected_sleep_candidate or {}).get("selection_reason"),
            final_sleep_context.get("duration_source"),
            (selected_sleep_candidate or {}).get("candidate_date"),
        )
        audit.info("[TodayAdvice][TodayMatch] sleep_hours=%s bedtime=%s sleep_score=%s", today_sleep_hours, today_bedtime, today_sleep_score)
        audit.info(
            "[TodayAdvice][TodayMatch] matched=%s ids=%s risk=%s focus=%s",
            today_match_payload["matched_patterns_count"],
            safe_json(today_match_payload["matched_pattern_ids"]),
            today_match_payload["risk_level"],
            today_match_payload["primary_focus"],
        )
        audit.put("analysis_json", analysis_json)
        audit.info("[TodayAdvice][AnalysisJSON] %s", safe_json(analysis_json))
        audit.dump_json("AnalysisJSON", analysis_json)

        try:
            today_advice = render_today_advice_from_analysis(
                analysis_json=analysis_json,
                model=final_model,
                chat_completion=_chat_completion,
            )
            if not (today_advice or "").strip():
                today_advice = "【fallback】Today advice の文章化に失敗しました（analysis_json は生成済み）。"
                analysis_json["fallback_used"] = True
                analysis_json["final_status"] = "fallback"
                analysis_json["stage_b_error"] = "empty_generation"
            else:
                analysis_json["fallback_used"] = False
                analysis_json["final_status"] = "success"
        except Exception as stage_b_exc:
            logging.warning("today_advice_stage_b_failed target_date=%s error=%s", target_date, stage_b_exc)
            today_advice = "【fallback】Today advice の文章化に失敗しました（analysis_json は生成済み）。"
            analysis_json["fallback_used"] = True
            analysis_json["final_status"] = "fallback"
            analysis_json["stage_b_error"] = type(stage_b_exc).__name__
        audit.put("final_text", {"text": today_advice})
        audit.info("[TodayAdvice][FinalText] %s", today_advice)
    except Exception as exc:
        logging.warning("today_advice_pipeline_failed target_date=%s error=%s", target_date, exc)
        exploratory_summary = {"matched_today_conditions": []}
        regression_summary = {"available": False, "sample_size": 0, "top_positive_risk_features": [], "top_protective_features": []}
        lightgbm_summary = {"available": False, "sample_size": 0, "feature_importances": [], "top_risk_features": [], "top_protective_features": [], "skipped_reason": "pipeline_failed"}
        analysis_json = {
            "target_date": target_date,
            "today_sleep_context": {"sleep_available": False, "sleep_invalid_reason": "pipeline_failed", "sleep_hours": None},
            "recent_7d_summary": {"behavior_trend": ["直近7日傾向はデータ不足"], "recording_trend": []},
            "exploratory_summary": {},
            "regression_summary": regression_summary,
            "lightgbm_summary": lightgbm_summary,
            "risk_level": "unknown",
            "primary_focus": "不明",
            "skipped_reason": "analysis_failed",
            "fallback_used": False,
            "final_status": "failed",
        }
        today_advice = ""
        audit.put("regression", summarize_regression(regression_summary))
        audit.put("analysis_json", analysis_json)
        audit.put("final_text", {"text": today_advice})
        audit.info("[TodayAdvice][AnalysisJSON] %s", safe_json(analysis_json))
        audit.info("[TodayAdvice][FinalText] %s", today_advice)
    audit.put("notes_fallback_reason_counts", audit.payload["analysis_audit"].get("notes_labeling", {}).get("fallback_reason_counts", {}))
    audit.put("sleep_feature_conversion_samples", audit.payload["analysis_audit"].get("features", {}).get("sleep_feature_conversion_samples", []))
    audit.put("matched_patterns_count", int(analysis_json.get("matched_patterns_count", 0)))
    audit.put("evidence_used", list(analysis_json.get("evidence_used", [])))
    fallback_used = bool(analysis_json.get("fallback_used", False))
    logging.info("today_advice_fallback_used=%s", fallback_used)
    audit.emit_final()
    judgment_json = {
        "analysis_json": analysis_json,
        "analysis_audit": audit.payload["analysis_audit"],
        "matched_pattern_count": len(exploratory_summary.get("matched_today_conditions", [])),
        "matched_patterns_count": int(analysis_json.get("matched_patterns_count", 0)),
        "evidence_used": list(analysis_json.get("evidence_used", [])),
        "meal_signal": analysis_json.get("meal_signal", ""),
        "notes_pattern_signal": analysis_json.get("notes_pattern_signal", ""),
        "location_pattern_signal": analysis_json.get("location_pattern_signal", ""),
        "regression_summary": regression_summary,
        "lightgbm_summary": lightgbm_summary,
    }
    judgment_text = json.dumps(judgment_json, ensure_ascii=False)
    return MoodAdviceResult(
        today_advice=today_advice,
        judgment_json=judgment_json,
        judgment_text=judgment_text,
        high_mood_sample_count=structured["high_mood_sample_count"],
        low_mood_sample_count=structured["low_mood_sample_count"],
        history_count=len(history),
    )
