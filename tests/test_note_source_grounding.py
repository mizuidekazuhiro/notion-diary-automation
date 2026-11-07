import json

import pytest

from publish.read_daily_log import parse_daily_log_payload
from scripts.note_batch_labeler import label_notes_in_batches
from scripts.today_advice_feature_builder import build_daily_feature_table


def _summary(text):
    return parse_daily_log_payload({"found": True, "target_date": "2000-01-02", "notes": text}, target_date="2000-01-02")


def _positive_reply(**kwargs):
    rows = json.loads(kwargs["user_prompt"])["rows"]
    return json.dumps({"rows": [{
        "id": row["id"], "date": row["date"], "sentiment": "positive",
        "flags": {"fatigue": False, "stress": False, "sleep_issue": False, "achievement": True, "self_care": False, "social_load": False},
        "tags": ["achievement", "productive"] + (["gym"] if "ジム" in row["text"] else []), "confidence": 0.95,
    } for row in rows]})


@pytest.mark.parametrize("text,expected", [
    ("コーヒーを飲んだ。協議タスクを完了に変更したが、実施日は未確認。", False),
    ("タスクを完了に変更できた。実施日は未確認。", False),
    ("タスクをDropに変更した。", False),
    ("資料を書き上げた。協議タスクを完了に変更したが、実施日は未確認。", True),
    ("課題を終え、達成感を感じました。", True),
    ("できたことはなかった。タスクを完了に変更した。", False),
])
def test_achievement_requires_independent_reported_outcome(monkeypatch, text, expected):
    monkeypatch.setenv("NOTES_LABEL_CACHE_DISABLE", "1")
    audit = {}
    result = label_notes_in_batches([_summary(text)], chat_completion=_positive_reply, model="test-model", audit=audit)
    assert result["2000-01-02"].achievement_flag is expected
    assert audit["source_grounding_rejections"].get("unsupported_achievement", 0) == int(not expected)
    assert audit["parse_error"] is False


def test_grounded_cache_reuse_keeps_exercise_and_does_not_repeat_ai(monkeypatch, tmp_path):
    monkeypatch.delenv("NOTES_LABEL_CACHE_DISABLE", raising=False)
    monkeypatch.setenv("NOTES_LABEL_CACHE_DIR", str(tmp_path))
    summary = _summary("ジムで筋トレをした。協議タスクを完了に変更したが、実施日は未確認。")
    first_audit = {}
    first = label_notes_in_batches([summary], chat_completion=_positive_reply, model="test-model", audit=first_audit)
    def no_second_call(**_kwargs):
        raise AssertionError("unchanged normalized label must use cache")
    second_audit = {}
    second = label_notes_in_batches([summary], chat_completion=no_second_call, model="test-model", audit=second_audit)
    assert second[summary.target_date].achievement_flag is False
    assert second_audit["cache_hit_count"] == 1
    assert second_audit["api_calls"] == 0
    assert second_audit["source_grounding_rejections"]["unsupported_achievement"] == 1
    features = build_daily_feature_table([summary], second)
    assert bool(features.iloc[0]["notes_achievement_flag"]) is False
    assert bool(features.iloc[0]["notes_has_exercise"]) is True
