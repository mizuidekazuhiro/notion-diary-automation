from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class FRiskEventCase:
    event_date: str
    event_type: str
    pre_rows: list[dict[str, Any]]
    event_row: dict[str, Any]
    post_rows: list[dict[str, Any]]
    pre_signature: dict[str, Any]
    post_signature: dict[str, Any]


def build_f_event_cases(train_df: Any, *, pre_days: int, post_days: int) -> list[FRiskEventCase]:
    cases: list[FRiskEventCase] = []
    event_indexes = [i for i, v in enumerate(train_df["f_event_flag"].tolist()) if int(v) == 1]
    seen: set[str] = set()
    for idx in event_indexes:
        event_row = train_df.iloc[idx].to_dict()
        event_date = str(event_row.get("date") or "")
        if not event_date or event_date in seen:
            continue
        seen.add(event_date)
        pre_start = max(0, idx - pre_days)
        post_end = min(len(train_df), idx + post_days + 1)
        pre_rows = [dict(r) for r in train_df.iloc[pre_start:idx].to_dict("records")]
        post_rows = [dict(r) for r in train_df.iloc[idx + 1:post_end].to_dict("records")]
        event_type = classify_f_event_type(event_row=event_row, pre_rows=pre_rows)
        cases.append(
            FRiskEventCase(
                event_date=event_date,
                event_type=event_type,
                pre_rows=pre_rows,
                event_row=event_row,
                post_rows=post_rows,
                pre_signature=build_window_signature(pre_rows),
                post_signature=build_window_signature(post_rows),
            )
        )
    return cases


def build_recent_case_signature(work_df: Any, *, pre_days: int) -> dict[str, Any]:
    recent_rows = [dict(r) for r in work_df.tail(pre_days).to_dict("records")]
    return {
        "recent_rows": recent_rows,
        "pre_signature": build_window_signature(recent_rows),
        "target_date": str(recent_rows[-1].get("date") or "") if recent_rows else "",
    }


def build_window_signature(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"signals": [], "sequence": []}
    signal_hits: list[str] = []
    sequence: list[list[str]] = []
    key_signals = [
        ("f_cluster", lambda r: bool(r.get("f_event_cluster_flag"))),
        ("recent_f", lambda r: 0 < float(r.get("days_since_last_f") or 999) <= 7),
        ("sleep_short", lambda r: float(r.get("sleep_short_streak") or 0) >= 2),
        ("stress", lambda r: bool(r.get("notes_stress_flag"))),
        ("fatigue", lambda r: bool(r.get("notes_fatigue_flag"))),
        ("social", lambda r: bool(r.get("notes_social_load_flag"))),
        ("drinking", lambda r: bool(r.get("notes_has_drinking"))),
        ("late_outing", lambda r: bool(r.get("late_outing_flag"))),
        ("spending_spike", lambda r: float(r.get("spending_vs_7d_delta") or 0) > 2500),
    ]
    for row in rows:
        day_signals: list[str] = []
        for name, predicate in key_signals:
            try:
                hit = bool(predicate(row))
            except Exception:
                hit = False
            if hit:
                day_signals.append(name)
                signal_hits.append(name)
        sequence.append(day_signals)
    return {
        "signals": sorted(set(signal_hits)),
        "sequence": sequence,
    }


def classify_f_event_type(*, event_row: dict[str, Any], pre_rows: Sequence[dict[str, Any]]) -> str:
    merchants = str(event_row.get("expense_f_merchants") or "").lower()
    categories = str(event_row.get("expense_f_categories") or "").lower()
    location = str(event_row.get("location_summary") or "").lower()
    place = str(event_row.get("place") or "").lower()
    first_time = str(event_row.get("expense_f_first_time") or "").lower()
    last_time = str(event_row.get("expense_f_last_time") or "").lower()

    late_outing = bool(event_row.get("late_outing_flag")) or any(bool(r.get("late_outing_flag")) for r in pre_rows)
    social = bool(event_row.get("social_spend_like_flag")) or any(bool(r.get("notes_has_social")) for r in pre_rows)
    drinking = bool(event_row.get("notes_has_drinking")) or ("bar" in merchants or "居酒屋" in merchants)
    stress = bool(event_row.get("notes_stress_flag")) or any(bool(r.get("notes_stress_flag")) for r in pre_rows)
    detour = bool(event_row.get("multi_stop_flag")) or ("station" in merchants or "駅" in merchants)
    spend_delta = float(event_row.get("spending_vs_7d_delta") or 0)

    late_time = any(t in (first_time + " " + last_time) for t in ["22:", "23:", "00:", "01:"])

    if late_outing and (social or late_time):
        return "night_outing"
    if drinking and social:
        return "drinking_social"
    if stress:
        return "stress_release"
    if spend_delta >= 3500 and not social:
        return "impulse_spend"
    if detour or any(w in (categories + " " + location + " " + place) for w in ["commute", "通勤", "駅"]):
        return "commute_detour"
    return "unknown"
