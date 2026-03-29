from __future__ import annotations

import pandas as pd

from scripts.f_risk_case_patterns import build_f_event_cases, build_recent_case_signature


def _base_rows():
    rows = []
    for i in range(12):
        rows.append(
            {
                "date": f"2026-01-{i+1:02d}",
                "f_event_flag": 1 if i in {5, 9} else 0,
                "expense_f_merchants": "居酒屋" if i == 5 else "",
                "expense_f_categories": "food",
                "expense_f_first_time": "23:10",
                "expense_f_last_time": "23:50",
                "notes_has_drinking": i in {4, 5},
                "notes_social_load_flag": i in {4, 5},
                "notes_stress_flag": i in {8, 9},
                "late_outing_flag": i in {4, 5},
                "multi_stop_flag": i == 9,
                "spending_vs_7d_delta": 4200 if i == 9 else 200,
            }
        )
    return rows


def test_build_f_event_cases_includes_pre_post_and_signatures():
    df = pd.DataFrame(_base_rows())
    cases = build_f_event_cases(df, pre_days=3, post_days=2)
    assert len(cases) == 2
    first = cases[0]
    assert first.event_date == "2026-01-06"
    assert len(first.pre_rows) == 3
    assert len(first.post_rows) == 2
    assert "signals" in first.pre_signature


def test_event_type_classification_has_known_labels():
    df = pd.DataFrame(_base_rows())
    cases = build_f_event_cases(df, pre_days=3, post_days=2)
    labels = {c.event_date: c.event_type for c in cases}
    assert labels["2026-01-06"] in {"night_outing", "drinking_social"}
    assert labels["2026-01-10"] in {"stress_release", "impulse_spend", "commute_detour"}


def test_recent_signature_contains_recent_flow():
    df = pd.DataFrame(_base_rows())
    recent = build_recent_case_signature(df, pre_days=3)
    assert len(recent["recent_rows"]) == 3
    assert isinstance(recent["pre_signature"]["sequence"], list)
