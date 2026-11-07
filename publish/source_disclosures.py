from __future__ import annotations

from typing import Any
from scripts.data_status import UNAVAILABLE, metric_status, source_states


def source_disclosures(summary: Any, expense_f: dict | None, f_risk: dict | None) -> list[tuple[str, str]]:
    result = []
    for group, label in (("sleep", "睡眠"), ("nutrition", "食事の栄養")):
        status = source_states(summary).get(group, {}).get("status")
        if status in {"query_failed", "parse_failed", "invalid"}:
            result.append((group, f"{label}：取得または読み取りを完了できず、保存済みの値を今回の判定には使っていません。"))
        elif status in {"missing", "unknown", "stale"}:
            result.append((group, f"{label}：この日の記録を確認できません。0とは扱いません。"))
    if expense_f and expense_f.get("data_status") not in {"ok", "no_results"}:
        result.append(("expense_f", "支出の要注意項目：取得を確認できず、0件とは判断できません。"))
    if f_risk and f_risk.get("data_status") != "ok":
        result.append(("f_risk", "今日の支出リスク：比較できる記録が不足、または確認を完了できないため、判定を控えます。"))
    if metric_status(summary, "study", getattr(summary, "study_minutes", None)) in UNAVAILABLE:
        result.append(("study", "学習（司法試験 Study）：記録が届いているか確認できず、0分とは判断できません。"))
    if metric_status(summary, "workout", getattr(summary, "workout_duration_min", None)) in UNAVAILABLE:
        result.append(("workout", "筋トレ：記録が届いているか確認できず、実施なしとは判断できません。"))
    return result
