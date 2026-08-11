from __future__ import annotations

import pytest

from scripts.f_risk_case_similarity import FEATURE_KEYS
from scripts.f_risk_generator import (
    _add_f_recurrence_features,
    _build_prediction_row,
    _calculate_f_recurrence_features,
    _rule_based_fallback,
    _score_rule_based_risk,
)


def test_recurrence_features_use_only_events_before_each_day() -> None:
    features = _calculate_f_recurrence_features(
        [
            ("2026-01-01", 1),
            ("2026-01-05", 1),
            ("2026-01-07", 1),
            ("2026-01-08", 0),
            ("2026-01-16", 0),
        ]
    )

    assert features[0] == {
        "days_since_last_f": None,
        "f_event_count_rolling_7d": 0,
        "f_event_count_rolling_14d": 0,
        "f_event_count_rolling_30d": 0,
        "f_event_cluster_flag": 0,
    }
    assert features[1]["days_since_last_f"] == 4
    assert features[1]["f_event_count_rolling_7d"] == 1
    # The F on January 7 is the outcome for that row, not an input feature.
    assert features[2]["f_event_count_rolling_7d"] == 2
    assert features[2]["f_event_cluster_flag"] == 1
    assert features[3]["days_since_last_f"] == 1
    assert features[3]["f_event_count_rolling_7d"] == 3
    assert features[4]["days_since_last_f"] == 9
    assert features[4]["f_event_count_rolling_7d"] == 0
    assert features[4]["f_event_count_rolling_14d"] == 2
    assert features[4]["f_event_cluster_flag"] == 0


def test_prediction_row_recomputes_recurrence_for_prediction_date() -> None:
    pd = pytest.importorskip("pandas")
    train = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-05", "2026-01-07"],
            "f_event_flag": [1, 1, 1],
            "expense_f_count": [1, 1, 1],
            "expense_f_total": [1000, 1000, 1000],
            "spending_total": [1000, 1000, 1000],
            "spending_vs_7d_delta": [0, 0, 0],
        }
    )
    enriched = _add_f_recurrence_features(train)

    prediction = _build_prediction_row(
        enriched,
        prediction_date="2026-01-08",
        daily_log_context_date="2026-01-07",
    ).iloc[0]

    assert prediction["days_since_last_f"] == 1
    assert prediction["f_event_count_rolling_7d"] == 3
    assert prediction["f_event_count_rolling_14d"] == 3
    assert prediction["f_event_cluster_flag"] == 1
    assert pd.isna(prediction["expense_f_count"])


def test_recurrence_features_affect_rules_and_fallback() -> None:
    clustered = {
        "days_since_last_f": 2,
        "f_event_count_rolling_7d": 2,
        "f_event_cluster_flag": 1,
    }
    rule = _score_rule_based_risk(clustered)
    assert rule["score"] == 4
    assert rule["hits"] == ["days_since_last_f<=3", "f_event_cluster_flag"]

    fallback = _rule_based_fallback(clustered, availability={"unavailable_groups": []})
    assert fallback["risk_matched"] is True
    assert fallback["blended_score"] == 0.65

    cooled_down = _score_rule_based_risk({"days_since_last_f": 15, "f_event_cluster_flag": 0})
    assert cooled_down["score"] == -1
    assert cooled_down["protective_hits"] == ["days_since_last_f>=14"]


def test_case_similarity_includes_all_recurrence_features() -> None:
    for feature in (
        "days_since_last_f",
        "f_event_count_rolling_7d",
        "f_event_count_rolling_14d",
        "f_event_count_rolling_30d",
        "f_event_cluster_flag",
    ):
        assert feature in FEATURE_KEYS
