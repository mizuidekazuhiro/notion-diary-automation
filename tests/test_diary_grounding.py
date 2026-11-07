import json

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
