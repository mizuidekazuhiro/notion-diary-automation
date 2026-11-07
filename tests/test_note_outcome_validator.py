import json

import pytest

from scripts.note_outcome_validator import validate_outcome_candidates

ROWS = [{"id": "one", "text": "資料を完成させた。", "candidate_tags": ["achievement"]}]


def test_empty_candidates_skip_ai():
    def forbidden(**_kwargs):
        raise AssertionError("no candidates must not call AI")
    assert validate_outcome_candidates([], chat_completion=forbidden).status == "skipped"


@pytest.mark.parametrize("row", [{"text": "資料を完成させた。", "candidate_tags": ["achievement"]},
                                  {"id": "one", "text": "資料", "candidate_tags": [[]]}])
def test_invalid_candidates_fail_before_ai(row):
    calls = []
    result = validate_outcome_candidates([row], chat_completion=lambda **_: calls.append(1))
    assert result.status == "failed"
    assert result.error_code == "invalid_candidate"
    assert calls == []


def test_quotes_must_come_from_the_correct_note():
    def respond(**kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        return json.dumps({"rows": [{"id": "one", "decisions": [{"tag": "achievement", "grounded": True, "quote": "資料を完成させた"}]}]})
    result = validate_outcome_candidates(ROWS, chat_completion=respond)
    assert result.status == "success"
    assert result.accepted_quotes == {"one": {"achievement": "資料を完成させた"}}


@pytest.mark.parametrize("output", [
    {"rows": []},
    {"rows": [{"id": "other", "decisions": []}]},
    {"rows": [{"id": "one", "decisions": [{"tag": "achievement", "grounded": True, "quote": "リラックスした"}]}]},
    {"rows": [{"id": "one", "decisions": [{"tag": "achievement", "grounded": "true", "quote": "資料"}]}]},
])
def test_malformed_or_untraceable_verification_is_failed(output):
    result = validate_outcome_candidates(ROWS, chat_completion=lambda **_: json.dumps(output))
    assert result.status == "failed"
    assert result.accepted_quotes == {}
    assert result.error_code


def test_transport_failure_does_not_log_input_or_exception_body(caplog):
    marker = "private diagnostic marker"
    def fail(**_kwargs):
        raise TimeoutError(marker)
    result = validate_outcome_candidates(ROWS, chat_completion=fail)
    assert result.status == "failed"
    assert result.error_type == "TimeoutError"
    assert marker not in caplog.text
