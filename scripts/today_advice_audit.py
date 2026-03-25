from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping, Optional


def is_today_advice_debug_enabled() -> bool:
    return (os.getenv("TODAY_ADVICE_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"})


class TodayAdviceAuditLogger:
    def __init__(self, *, target_date: str, debug: bool) -> None:
        self.target_date = target_date
        self.debug = debug
        self.payload: dict[str, Any] = {"analysis_audit": {"target_date": target_date}}

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        self.payload["analysis_audit"][key] = dict(value)

    def info(self, message: str, *args: Any) -> None:
        logging.info(message, *args)

    def dump_json(self, key: str, value: Mapping[str, Any]) -> None:
        if not self.debug:
            return
        self.info("[TodayAdvice][%s] %s", key, json.dumps(value, ensure_ascii=False, default=str))

    def emit_final(self) -> None:
        if self.debug:
            self.info("[TodayAdvice][AnalysisAudit] %s", json.dumps(self.payload, ensure_ascii=False, default=str))


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def count_missing(value: object) -> int:
    if value is None:
        return 1
    if isinstance(value, str) and not value.strip():
        return 1
    return 0


def summarize_regression(regression_summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(regression_summary.get("available", False)),
        "sample_size": int(regression_summary.get("sample_size", 0) or 0),
        "regression_target_name": regression_summary.get("regression_target_name") or "next_day_low_mood_flag",
        "regression_feature_names": list(regression_summary.get("regression_feature_names") or []),
        "top_positive_features": list(regression_summary.get("top_positive_features") or regression_summary.get("top_positive_risk_features") or []),
        "top_negative_features": list(regression_summary.get("top_negative_features") or regression_summary.get("top_protective_features") or []),
        "skipped_reason": regression_summary.get("skipped_reason"),
    }
