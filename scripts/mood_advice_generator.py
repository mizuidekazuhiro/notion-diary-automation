from __future__ import annotations

import importlib
import json
import logging
import math
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from publish.read_daily_log import DailyLogSummary, parse_daily_log_payload, read_daily_log
from ingest.http_client import fetch_json
from urllib.parse import urlencode, urlsplit, urlunsplit
from scripts.note_batch_labeler import NoteClassificationUnavailable, label_notes_in_batches
from scripts.notes_quality import assess_notes_quality
from scripts.note_outcome_validator import OutcomeValidationUnavailable
from scripts.openai_chat_utils import (
    _build_chat_messages,
    chat_completion as shared_chat_completion,
)
from scripts.today_advice_feature_builder import build_daily_feature_table
from scripts.today_advice_pattern_analyzer import analyze_exploratory_patterns
from scripts.today_advice_regression import run_low_mood_regression
from scripts.today_advice_lightgbm import run_lightgbm_low_mood
from scripts.today_advice_renderer import build_analysis_json, render_today_advice_result
from scripts.ai_models import model_for
from scripts.today_advice_context import (
    AdviceJudgment,
    TodayAdviceContext,
    LOOKBACK_DAYS,
    RECENT_WINDOW_DAYS,
    SHORT_WINDOW_DAYS,
    SAMPLE_DAYS_PER_BUCKET,
    MEAL_NUMERIC_FIELDS,
    NOTES_SIGNAL_PATTERNS,
    LOCATION_PATTERN_KEYS,
    _safe_text,
    _safe_float,
    _safe_int,
    normalize_mood_to_score,
    _mean,
    _delta,
    _recording_rate,
    _format_number,
    _resolve_today_sleep_candidates,
    _build_metric_snapshot,
    _compare_metric_windows,
    _trend_direction,
    _meal_metric_avg,
    _extract_notes_signals,
    _notes_signal_rates,
    _extract_location_patterns,
    _location_pattern_rates,
    _workout_snapshot,
    _build_behavior_snapshot,
    _delta_map,
    _build_today_state,
    _build_day_record,
    _select_top_days,
    _build_structured_comparison,
)
from scripts.today_advice_audit import (
    TodayAdviceAuditLogger,
    count_missing,
    is_today_advice_debug_enabled,
    safe_json,
    summarize_regression,
)

# Retained legacy import constants/prompts. The live pipeline selects models
# through ai_models.model_for and renders through today_advice_renderer.
OPENAI_TIMEOUT = (5, 90)
DEFAULT_MINI_MODEL = "gpt-4.1-mini"
DEFAULT_FINAL_MODEL = "gpt-4.1"


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
    logging.info("today_advice_debug_private_content_omitted kind=%s stage=%s model=%s", debug_kind, stage, model)


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
    judgment_json: AdviceJudgment
    judgment_text: str
    high_mood_sample_count: int
    low_mood_sample_count: int
    history_count: int


@dataclass(frozen=True)
class SafeDailyLogReadResult:
    summary: Optional[DailyLogSummary]
    failed: bool
    error_type: Optional[str] = None


@dataclass(frozen=True)
class TodayAdviceHistoryLoadResult:
    summaries: list[DailyLogSummary]
    requested_dates: list[str]
    failed_dates: list[str]
    missing_dates: list[str]
    include_next_day: bool


def _safe_read_daily_log_for_advice(*, daily_log_read_url: str, bearer_token: Optional[str], day: str) -> SafeDailyLogReadResult:
    try:
        return SafeDailyLogReadResult(summary=read_daily_log(
            daily_log_read_url=daily_log_read_url,
            target_date=day,
            bearer_token=bearer_token,
        ), failed=False)
    except Exception as exc:
        logging.warning(
            "today_advice_history_read_skipped date=%s reason=%s error=%s",
            day,
            type(exc).__name__,
            "source_read_failed",
        )
        return SafeDailyLogReadResult(summary=None, failed=True, error_type=type(exc).__name__)


def _load_daily_logs_for_period_with_debug(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: str,
    days: int = LOOKBACK_DAYS,
    include_next_day: bool = False,
) -> TodayAdviceHistoryLoadResult:
    base_day = datetime.strptime(target_date, "%Y-%m-%d")
    requested_dates: list[str] = []
    if include_next_day:
        requested_dates.append((base_day + timedelta(days=1)).strftime("%Y-%m-%d"))
    for offset in range(days):
        requested_dates.append((base_day - timedelta(days=offset)).strftime("%Y-%m-%d"))

    summaries: list[DailyLogSummary] = []
    failed_dates: list[str] = []
    missing_dates: list[str] = []

    started = time.perf_counter()
    parts = urlsplit(daily_log_read_url)
    if parts.scheme in {"https", "http"} and os.getenv("TODAY_ADVICE_HISTORY_MODE", "bulk") == "bulk":
        query = urlencode({"start": min(requested_dates), "end": max(requested_dates)})
        url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/history", query, ""))
        try:
            payload = fetch_json(url, bearer_token)
            if not isinstance(payload.get("items"), list):
                raise ValueError("history_contract_invalid")
            by_date: dict[str, list[dict[str, Any]]] = {}
            for item in payload["items"]:
                day = item.get("target_date") if isinstance(item, dict) else None
                if day in requested_dates:
                    by_date.setdefault(day, []).append(item)
            for day in requested_dates:
                candidates = by_date.get(day, [])
                if not candidates:
                    missing_dates.append(day)
                    continue
                # Contradictory pages are excluded, rather than multiplying support
                # or guessing which outcome/measurement is true.
                observations = [{key: value for key, value in item.items() if key not in {"page_id", "title", "last_edited_time"}}
                                for item in candidates]
                if any(row != observations[0] for row in observations[1:]):
                    failed_dates.append(day)
                    continue
                parsed = parse_daily_log_payload({**candidates[0], "found": True}, target_date=day)
                if parsed is not None:
                    summaries.append(parsed)
            logging.info("today_advice_history_load_done mode=bulk requests=1 loaded=%s missing=%s failed=%s elapsed_ms=%s",
                         len(summaries), len(missing_dates), len(failed_dates), int((time.perf_counter()-started)*1000))
            return TodayAdviceHistoryLoadResult(summaries, requested_dates, failed_dates, missing_dates, include_next_day)
        except Exception as exc:
            logging.warning("today_advice_history_bulk_failed error_type=%s", type(exc).__name__)
            # Failure is not absence. Do not fan out 31 requests into a failing API.
            return TodayAdviceHistoryLoadResult([], requested_dates, list(requested_dates), [], include_next_day)

    logging.info("today_advice_history_load_start target_date=%s days=%s include_next_day=%s", target_date, days, include_next_day)
    for day in requested_dates:
        result = _safe_read_daily_log_for_advice(daily_log_read_url=daily_log_read_url, bearer_token=bearer_token, day=day)
        if result.failed:
            failed_dates.append(day)
            continue
        if result.summary is None:
            missing_dates.append(day)
            logging.info("today_advice_history_missing date=%s", day)
            continue
        summaries.append(result.summary)

    partial = bool(failed_dates)
    incomplete = bool(failed_dates or missing_dates)
    logging.info(
        "today_advice_history_load_done target_date=%s requested_count=%s loaded_count=%s failed_count=%s missing_count=%s partial=%s incomplete=%s",
        target_date,
        len(requested_dates),
        len(summaries),
        len(failed_dates),
        len(missing_dates),
        partial,
        incomplete,
    )
    if partial:
        logging.info("today_advice_history_partial=true target_date=%s failed_dates=%s", target_date, failed_dates)
    if incomplete and not partial:
        logging.info("today_advice_history_incomplete=true target_date=%s missing_dates=%s", target_date, missing_dates)

    return TodayAdviceHistoryLoadResult(
        summaries=summaries,
        requested_dates=requested_dates,
        failed_dates=failed_dates,
        missing_dates=missing_dates,
        include_next_day=include_next_day,
    )


def load_daily_logs_for_period(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: str,
    days: int = LOOKBACK_DAYS,
    include_next_day: bool = False,
) -> list[DailyLogSummary]:
    result = _load_daily_logs_for_period_with_debug(
        daily_log_read_url=daily_log_read_url,
        bearer_token=bearer_token,
        target_date=target_date,
        days=days,
        include_next_day=include_next_day,
    )
    return result.summaries


def build_today_advice_generation_context(
    *,
    daily_log_read_url: str,
    bearer_token: Optional[str],
    target_date: str,
) -> Optional[TodayAdviceContext]:
    history_result = _load_daily_logs_for_period_with_debug(
        daily_log_read_url=daily_log_read_url,
        bearer_token=bearer_token,
        target_date=target_date,
        days=LOOKBACK_DAYS,
        include_next_day=True,
    )
    history = history_result.summaries
    if not history:
        return None

    today_summary = next((item for item in history if item.target_date == target_date), None)
    if today_summary is None:
        logging.warning("today_advice_target_page_missing target_date=%s", target_date)
        return None
    historical_summaries = [item for item in history if item.target_date <= target_date]
    advice_target_date = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    sleep_candidates, selected_sleep_candidate, sleep_properties_source = _resolve_today_sleep_candidates(
        target_date=advice_target_date,
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
        for key in ("sleep_duration_min", "sleep_score", "sleep_start", "sleep_end"):
            today_state["today_sleep"][key] = None
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
    history_debug = {
        "history_requested_days": len(history_result.requested_dates),
        "history_loaded_count": len(history_result.summaries),
        "history_failed_count": len(history_result.failed_dates),
        "history_missing_count": len(history_result.missing_dates),
        "history_failed_dates": history_result.failed_dates,
        "history_missing_dates": history_result.missing_dates,
        "history_partial": bool(history_result.failed_dates),
        "history_incomplete": bool(history_result.failed_dates or history_result.missing_dates),
        "include_next_day": history_result.include_next_day,
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
        "history_debug": history_debug,
        "reflection_date": target_date,
        "advice_target_date": advice_target_date,
    }




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
    generation_context: Mapping[str, Any] | None = None,
) -> Optional[MoodAdviceResult]:
    context = generation_context if generation_context is not None else build_today_advice_generation_context(
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
    final_model = model_for("advice")
    mini_model = model_for("notes")

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
            outcome_chat_completion=shared_chat_completion,
            model=mini_model,
            raw_response_dir=notes_debug_dir,
            audit=note_label_audit,
        )
        notes_quality = assess_notes_quality(
            historical_summaries=historical_summaries, note_labels=note_labels,
            note_label_audit=note_label_audit, target_date=target_date, audit=audit,
        )
        note_labels = notes_quality.labels
        notes_payload = notes_quality.audit_metrics

        if note_label_audit.get("outcome_validation_status") == "failed":
            raise OutcomeValidationUnavailable("notes_outcome_validation_unavailable")
        if note_label_audit.get("classification_status") == "failed":
            raise NoteClassificationUnavailable("notes_classification_unavailable")

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
        lightgbm_today_overrides: dict[str, Any] = {}
        if today_sleep_context.get("sleep_hours") is not None:
            lightgbm_today_overrides["sleep_hours"] = today_sleep_context.get("sleep_hours")
        if today_sleep_context.get("sleep_score") is not None:
            lightgbm_today_overrides["sleep_score"] = today_sleep_context.get("sleep_score")
        lightgbm_summary = run_lightgbm_low_mood(
            feature_df,
            today_feature_overrides=lightgbm_today_overrides or None,
            today_feature_date=context["advice_target_date"],
        )
        audit.put("lightgbm", lightgbm_summary)
        audit.info(
            "[TodayAdvice][LightGBM] available=%s sample=%s reason=%s skipped_columns=%s feature_columns=%s",
            lightgbm_summary.get("available"),
            lightgbm_summary.get("sample_size"),
            lightgbm_summary.get("skipped_reason"),
            safe_json(lightgbm_summary.get("skipped_columns", [])),
            safe_json(lightgbm_summary.get("feature_columns", [])),
        )
        audit.info(
            "[TodayAdvice][LightGBM] selected_candidate_date=%s feature_row_date_used_as_today=%s explanation_source_row_date=%s contribution_scope=%s",
            (selected_sleep_candidate or {}).get("candidate_date"),
            lightgbm_summary.get("feature_row_date_used_as_today"),
            lightgbm_summary.get("lightgbm_explanation_source_row_date"),
            lightgbm_summary.get("contribution_feature_scope"),
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
        today_bedtime = today_sleep_context.get("bedtime")
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
            render_result = render_today_advice_result(
                analysis_json=analysis_json,
                model=final_model,
                chat_completion=_chat_completion,
            )
            today_advice = render_result.text
            analysis_json["generation"] = asdict(render_result)
            analysis_json["fallback_used"] = render_result.fallback_used
            analysis_json["final_status"] = render_result.status
            analysis_json["stage_b_error"] = render_result.rejection_reason
        except Exception as stage_b_exc:
            logging.warning("today_advice_stage_b_failed target_date=%s error_type=%s", target_date, type(stage_b_exc).__name__)
            today_advice = "今日への助言を作成できませんでした。日記の記録を確認してください。"
            analysis_json["fallback_used"] = True
            analysis_json["final_status"] = "fallback"
            analysis_json["stage_b_error"] = type(stage_b_exc).__name__
        audit.put("final_text", {"text": today_advice})
        audit.info("[TodayAdvice][FinalText] %s", today_advice)
    except Exception as exc:
        logging.warning("today_advice_pipeline_failed target_date=%s error_type=%s", target_date, type(exc).__name__)
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
            "failure_stage": "notes_outcome_validation" if isinstance(exc, OutcomeValidationUnavailable) else "notes_classification" if isinstance(exc, NoteClassificationUnavailable) else "analysis",
            "fallback_used": True,
            "final_status": "failed",
        }
        today_advice = "記録の分析を完了できなかったため、今日への助言は控えます。"
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
