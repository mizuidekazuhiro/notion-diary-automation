from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts.note_batch_labeler import neutral_label
from scripts.notes_quality import assess_notes_quality
from scripts.today_advice_audit import TodayAdviceAuditLogger


@pytest.mark.parametrize("parse_rate,expected_quality,kept", [
    (0.799, "low", False), (0.8, "high", True), (1.0, "high", True),
])
def test_quality_boundary_quarantines_unreliable_labels_without_changing_metrics(parse_rate, expected_quality, kept):
    day = "2026-11-06"
    label = replace(neutral_label(day), sentiment_label="negative", sentiment_score=-1,
                    fatigue_flag=True, confidence="high", parse_quality="high", no_signal_note=False)
    audit = TodayAdviceAuditLogger(target_date=day, debug=False)
    result = assess_notes_quality(
        historical_summaries=[SimpleNamespace(target_date=day, notes="疲れた")],
        note_labels={day: label}, target_date=day, audit=audit,
        note_label_audit={"notes_parse_success_rate": parse_rate, "notes_date_merge_success_rate": 1.0,
                          "unknown_rate": 0.0, "top_tags": [("fatigue", 1)]},
    )
    assert result.audit_metrics["notes_quality_used"] == expected_quality
    assert result.labels[day].fatigue_flag is kept
    assert (result.labels[day].sentiment_label == "negative") is kept
    assert result.audit_metrics["dataframe_flag_counts"]["fatigue"] == 1
    assert result.audit_metrics["top_tags"] == [("fatigue", 1)]
    assert audit.payload["analysis_audit"]["notes_labeling"] == result.audit_metrics
    assert label.fatigue_flag is True  # no mutation of the classifier's original result
