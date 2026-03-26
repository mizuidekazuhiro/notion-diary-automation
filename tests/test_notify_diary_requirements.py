from __future__ import annotations

import importlib.util

from publish.email_templates import render_daily_log_text
from scripts.note_batch_labeler import parse_note_label_json
from scripts.today_advice_pattern_analyzer import LEAKAGE_COLUMNS


def test_drop_zero_does_not_render_none() -> None:
    text = render_daily_log_text({"target_date": "2026-03-20", "summary_text": "🎉\n- A (Priority: High)", "diary": "d", "meal_summary": "m"})
    assert "Drop: 0" in text
    drop_part = text.split("Drop: 0", 1)[1]
    assert "- None" not in drop_part
    assert "- —" not in drop_part.split("Meal summary", 1)[0]


def test_notes_structured_tags_extracted() -> None:
    raw = '[{"date":"2026-03-20","signals":[{"tag":"moderate_productivity","category":"state","polarity":"neutral","intensity":"medium","confidence":0.81,"evidence_text":"仕事はそこそこ"},{"tag":"early_home","category":"behavior","polarity":"positive","intensity":"medium","confidence":0.9,"evidence_text":"家に帰り"},{"tag":"gym","category":"behavior","polarity":"positive","intensity":"medium","confidence":0.95,"evidence_text":"ジム"}],"meta":{"parse_quality":"high"}}]'
    got = parse_note_label_json(raw, [{"date": "2026-03-20", "notes": "仕事はそこそこ。夜は家に帰りジム。"}])[0]
    tags = {s["tag"] for s in got.signals}
    assert {"moderate_productivity", "early_home", "gym"}.issubset(tags)
    assert got.derived_flags.get("exercise") is True


def test_unknown_is_not_collapsed_to_neutral() -> None:
    raw = '[{"date":"2026-03-20","signals":[],"meta":{"parse_quality":"low"}}]'
    got = parse_note_label_json(raw, [{"date": "2026-03-20", "notes": "..."}])[0]
    assert got.sentiment_label == "unknown"


def test_leakage_columns_configured() -> None:
    assert "next_day_low_mood_flag" in LEAKAGE_COLUMNS


def test_lightgbm_listed_in_requirements() -> None:
    req = open("requirements.txt", "r", encoding="utf-8").read()
    assert "lightgbm" in req


def test_email_section_order() -> None:
    text = render_daily_log_text(
        {
            "target_date": "2026-03-20",
            "summary_text": "🎉\n- A (Priority: High)",
            "today_advice": "adv",
            "diary": "diary",
            "sleep_analysis_jp": "sleep",
            "meal_summary": "meal",
        }
    )
    assert text.index("Today advice") < text.index("Diary") < text.index("Sleep & Condition") < text.index("Summary")
