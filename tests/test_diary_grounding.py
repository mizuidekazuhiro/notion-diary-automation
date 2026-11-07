import json

import pytest

from scripts import diary_generator as diary


def test_prompt_has_one_copy_and_study_workout_with_provenance():
    fields = {"Notes": "固有の本人メモ", "Study Minutes": "31.7", "Workout Duration Min": "42"}
    system, user = diary._build_prompts(fields, "2026-11-06")
    assert user.count("固有の本人メモ") == 1
    payload = json.loads(user)
    assert payload["sources"]["Study Minutes"]["value"] == "31.7"
    assert payload["sources"]["Workout Duration Min"]["value"] == "42"
    assert payload["sources"]["Notes"]["certainty"] == "reported"


def test_status_change_cannot_become_meeting_and_payment_cannot_become_emotion(monkeypatch):
    fields = {"Done Count": "1", "Done Tasks Detail": "協議する | done_date=2026-11-06 | event_date=null",
              "Expenses": "カフェ (500)", "Expenses Total": "500"}
    answer = {"sentences": [{"text": "取引先と協議し、カフェで一息ついた。", "sources": ["Done Tasks Detail", "Expenses"], "certainty": "recorded"}]}
    monkeypatch.setattr(diary, "chat_completion", lambda **kw: json.dumps(answer, ensure_ascii=False))
    result = diary.generate_diary_result(fields, "2026-11-06")
    assert result.fallback_used
    assert result.status == "degraded"
    assert "一息" not in result.text and "協議し" not in result.text
    assert "1件" in result.text and "500円" in result.text


def test_inference_cannot_be_promoted_and_unknown_sources_rejected(monkeypatch):
    fields = {"Location summary": "カフェにいた可能性がある"}
    answer = {"sentences": [{"text": "カフェで過ごした。", "sources": ["Location summary"], "certainty": "recorded"}]}
    monkeypatch.setattr(diary, "chat_completion", lambda **kw: json.dumps(answer, ensure_ascii=False))
    result = diary.generate_diary_result(fields, "2026-11-06")
    assert result.fallback_used
    assert "certainty_promoted" in result.rejection_reasons


def test_missing_important_study_is_rejected_even_if_pleasant_japanese(monkeypatch):
    answer = {"sentences": [{"text": "コーヒーを飲んだ。", "sources": ["Notes"], "certainty": "reported"}]}
    monkeypatch.setattr(diary, "chat_completion", lambda **kw: json.dumps(answer, ensure_ascii=False))
    result = diary.generate_diary_result({"Notes": "コーヒーを飲んだ", "Study Minutes": "31.7"}, "2026-11-06")
    assert result.fallback_used
    assert "missing_important_source" in result.rejection_reasons
    assert "31.7分" in result.text


def test_grounded_output_keeps_claim_sources_and_program_totals(monkeypatch):
    answers = iter([json.dumps({"sentences": [
        {"text": "コーヒーを飲んだ。", "sources": ["Notes"], "certainty": "reported"},
        {"text": "学習の記録は31.7分だった。", "sources": ["Study Minutes"], "certainty": "recorded"}]}),
        json.dumps({"grounded": True, "violations": []})])
    monkeypatch.setattr(diary, "chat_completion", lambda **kw: next(answers))
    result = diary.generate_diary_result({"Notes": "コーヒーを飲んだ", "Study Minutes": "31.7"}, "2026-11-06")
    assert result.status == "success" and not result.fallback_used
    assert result.evidence["claims"][1]["sources"] == ["Study Minutes"]
    assert result.evidence["inputs"]["Study Minutes"]["value"] == "31.7"


def test_displayed_inference_and_numeric_values_are_checked_without_ai_judgment():
    sources = diary.diary_sources({"Location summary": "カフェにいた可能性", "Study Minutes": "31.7"})
    assert "inference_not_disclosed" in diary.validate_sentences([
        {"text": "カフェで食事をした。", "sources": ["Location summary"], "certainty": "inferred"}], sources)
    assert "numeric_mismatch_or_omission" in diary.validate_sentences([
        {"text": "317分学習した。", "sources": ["Study Minutes"], "certainty": "recorded"}], sources)
    assert "numeric_mismatch_or_omission" in diary.validate_sentences([
        {"text": "記録を確認した。", "sources": ["Study Minutes"], "certainty": "recorded"}], sources)


def test_unknown_measurement_is_not_rendered_as_confirmed_zero():
    fields = {"Study Minutes": "0", "Data Status": '{"study":{"status":"query_failed"}}'}
    assert "学習時間の記録は0分" not in diary.safe_diary(fields, "2026-11-06")
    assert "Study Minutes" not in diary.diary_sources(fields)


def test_event_assertion_is_rejected_independently_of_certainty_or_emotion():
    sources = diary.diary_sources({"Done Tasks Detail": "取引先と協議する | done_date=2026-11-06 | event_date=null"})
    reasons = diary.validate_sentences([{"text": "完了タスクの取引先との協議を行った。",
                                        "sources": ["Done Tasks Detail"], "certainty": "status_change"}], sources)
    assert reasons == ["unsupported_event"]


@pytest.mark.parametrize("claim", [
    "滞在先で働きました。", "滞在先で働いていました。",
    "滞在先で仕事をしました。", "滞在先で勤務しました。",
])
def test_location_alone_does_not_establish_work_in_polite_or_continuous_form(claim):
    from scripts.diary_generator import diary_sources, validate_sentences

    sources = diary_sources({"Location Stays": "職場として記録された場所に滞在"})
    reasons = validate_sentences([
        {"text": claim, "sources": ["Location Stays"], "certainty": "recorded"},
    ], sources)
    assert "unsupported_activity_from_location" in reasons


def test_location_stay_and_personally_reported_work_remain_supported():
    from scripts.diary_generator import diary_sources, validate_sentences

    fields = {"Location Stays": "職場として記録された場所に滞在", "Notes": "滞在先で働きました。"}
    sources = diary_sources(fields)
    assert validate_sentences([
        {"text": "職場として記録された場所に滞在しました。", "sources": ["Location Stays"], "certainty": "recorded"},
    ], sources) == []
    assert validate_sentences([
        {"text": "滞在先で働きました。", "sources": ["Notes"], "certainty": "reported"},
    ], sources) == []


def test_unsupported_polite_work_claim_is_rejected_before_consistency_api(monkeypatch):
    import json
    from scripts import diary_generator

    calls = []
    def respond(**kwargs):
        calls.append(kwargs["model"])
        if len(calls) == 1:
            return json.dumps({"sentences": [{"text": "滞在先で働きました。", "sources": ["Location Stays"], "certainty": "recorded"}]})
        return json.dumps({"grounded": True, "violations": []})
    monkeypatch.setattr(diary_generator, "chat_completion", respond)
    result = diary_generator.generate_diary_result({"Location Stays": "職場として記録された場所に滞在"}, "2026-11-06")
    assert result.status == "degraded"
    assert result.fallback_used is True
    assert "unsupported_activity_from_location" in result.rejection_reasons
    assert len(calls) == 1
