from __future__ import annotations

from scripts.f_risk_case_patterns import FRiskEventCase
from scripts.f_risk_case_similarity import compute_case_similarity


def _case(event_date: str, event_type: str, pre_rows: list[dict]):
    return FRiskEventCase(
        event_date=event_date,
        event_type=event_type,
        pre_rows=pre_rows,
        event_row={},
        post_rows=[],
        pre_signature={"signals": ["sleep_short", "stress"], "sequence": [["sleep_short"], ["stress"]]},
        post_signature={},
    )


def test_case1_high_similarity_alert_ready_fields_present():
    recent = {
        "recent_rows": [
            {"sleep_short_streak": 2, "notes_stress_flag": True, "late_outing_flag": True, "notes_social_load_flag": True},
            {"sleep_short_streak": 3, "notes_stress_flag": True, "late_outing_flag": True, "notes_social_load_flag": True},
            {"sleep_short_streak": 3, "notes_stress_flag": True, "late_outing_flag": True, "notes_social_load_flag": True},
        ],
        "pre_signature": {"sequence": [["sleep_short"], ["sleep_short", "stress"], ["stress", "late_outing"]]},
    }
    cases = [_case("2026-03-03", "night_outing", recent["recent_rows"]), _case("2026-02-27", "night_outing", recent["recent_rows"])]
    sim = compute_case_similarity(recent_case=recent, event_cases=cases, top_n=3)
    assert sim["score_total"] >= 0.72
    assert sim["matched_case_dates"]
    assert sim["matched_pre_patterns"]


def test_case2_low_similarity_no_alert_condition():
    recent = {
        "recent_rows": [{"sleep_short_streak": 0, "notes_stress_flag": False, "late_outing_flag": False}],
        "pre_signature": {"sequence": [[]]},
    }
    cases = [_case("2026-03-03", "night_outing", [{"sleep_short_streak": 3, "notes_stress_flag": True, "late_outing_flag": True}])]
    sim = compute_case_similarity(recent_case=recent, event_cases=cases, top_n=3)
    assert sim["score_total"] < 0.55


def test_case3_and4_graceful_degradation_with_minimum_signals():
    recent = {
        "recent_rows": [{"sleep_short_streak": 2, "notes_stress_flag": False, "late_outing_flag": False, "multi_stop_flag": True}],
        "pre_signature": {"sequence": [["sleep_short"]]},
    }
    cases = [_case("2025-09-01", "commute_detour", [{"sleep_short_streak": 2, "multi_stop_flag": True}])]
    sim = compute_case_similarity(recent_case=recent, event_cases=cases, top_n=1)
    assert sim["usable_f_event_count"] == 1
    assert sim["matched_case_types"][0] == "commute_detour"


def test_case5_older_event_kept_when_in_input():
    recent = {"recent_rows": [{"notes_stress_flag": True}], "pre_signature": {"sequence": [["stress"]]}}
    old_case = _case("2025-01-10", "stress_release", [{"notes_stress_flag": True}])
    sim = compute_case_similarity(recent_case=recent, event_cases=[old_case], top_n=3)
    assert "2025-01-10" in sim["matched_case_dates"]


def test_case6_event_type_exposed_for_alert_text():
    recent = {"recent_rows": [{"notes_has_drinking": True, "notes_social_load_flag": True}], "pre_signature": {"sequence": [["drinking", "social"]]}}
    case = _case("2026-02-01", "drinking_social", [{"notes_has_drinking": True, "notes_social_load_flag": True}])
    sim = compute_case_similarity(recent_case=recent, event_cases=[case], top_n=1)
    assert sim["matched_case_types"] == ["drinking_social"]
