import json
from types import SimpleNamespace

import pytest

from scripts.generation_state import (
    assert_generation_readback,
    merge_generation_metadata,
    stage_generation_status,
)


@pytest.mark.parametrize("raw", [None, "", "null", "[]", "broken", '{"advice":null}', '{"advice":[]}'])
def test_absent_or_malformed_metadata_has_no_success_state(raw):
    summary = SimpleNamespace(generation_metadata=raw)
    assert stage_generation_status(summary, "advice") == ""
    saved = json.loads(merge_generation_metadata(summary, "diary", {"status": "failed"}))
    assert saved["diary"]["status"] == "failed"


def test_merge_preserves_other_stages_and_excludes_generated_text():
    summary = SimpleNamespace(generation_metadata=json.dumps({
        "sleep": {"status": "insufficient_data", "fallback_used": False},
        "advice": {"status": "success"},
    }))
    merged = merge_generation_metadata(summary, "advice", {
        "status": "degraded", "fallback_used": True, "text": "private generated text",
    })
    saved = json.loads(merged)
    assert saved["sleep"] == {"status": "insufficient_data", "fallback_used": False}
    assert saved["advice"] == {"status": "degraded", "fallback_used": True}
    assert "private generated text" not in merged
    assert stage_generation_status(SimpleNamespace(generation_metadata=merged), "advice") == "degraded"


def test_readback_uses_semantic_json_and_preserves_text_and_zero_contracts():
    summary = SimpleNamespace(
        diary="first\r\nsecond", study_minutes=0,
        generation_metadata='{"advice":{"fallback_used":false,"status":"insufficient_data"}}',
    )
    assert_generation_readback(summary, {
        "diary": "first\nsecond", "study_minutes": 0,
        "generation_metadata": '{"advice":{"status":"insufficient_data","fallback_used":false}}',
        "diary_generated_at": "ignored provider timestamp precision",
    })
    with pytest.raises(RuntimeError, match="fields=diary"):
        assert_generation_readback(summary, {"diary": "different private body"})
    with pytest.raises(RuntimeError, match="fields=generation_metadata"):
        assert_generation_readback(summary, {"generation_metadata": '{"advice":{"status":"success"}}'})
