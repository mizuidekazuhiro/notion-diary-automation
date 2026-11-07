from __future__ import annotations

from typing import Any
from scripts.data_status import UNAVAILABLE, metric_status


def source_disclosures(summary: Any, expense_f: dict | None, f_risk: dict | None) -> list[tuple[str, str]]:
    result = []
    if expense_f and expense_f.get("data_status") not in {"ok", "no_results"}:
        result.append(("expense_f", "支出の要注意項目：取得を確認できず、0件とは判断できません。"))
    if f_risk and f_risk.get("data_status") != "ok":
        result.append(("f_risk", "今日の支出リスク：比較できる記録が不足、または確認を完了できないため、判定を控えます。"))
    if metric_status(summary, "study", getattr(summary, "study_minutes", None)) in UNAVAILABLE:
        result.append(("study", "学習（司法試験 Study）：記録が届いているか確認できず、0分とは判断できません。"))
    if metric_status(summary, "workout", getattr(summary, "workout_duration_min", None)) in UNAVAILABLE:
        result.append(("workout", "筋トレ：記録が届いているか確認できず、実施なしとは判断できません。"))
    return result
