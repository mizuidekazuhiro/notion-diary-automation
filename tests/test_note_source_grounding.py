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


def _verifier(expected):
    def reply(**kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        rows = json.loads(kwargs["user_prompt"])["rows"]
        return json.dumps({"rows": [{"id": row["id"], "decisions": [
            {"tag": tag, "grounded": expected, "quote": row["text"] if expected else ""}
            for tag in row["candidate_tags"]]} for row in rows]})
    return reply


@pytest.mark.parametrize("text,expected", [
    ("コーヒーを飲んだ。協議タスクを完了に変更したが、実施日は未確認。", False),
    ("タスクを完了に変更できた。実施日は未確認。", False),
    ("タスクをDropに変更した。", False),
    ("資料を書き上げた。協議タスクを完了に変更したが、実施日は未確認。", True),
    ("課題を終え、達成感を感じました。", True),
    ("できたことはなかった。タスクを完了に変更した。", False),
    ("協議タスクを完了にできた。実施日は未確認。", False),
    ("資料が完成したわけではない。", False),
    ("資料が完成したかもしれない。", False),
    ("資料を完成させて、達成感を覚えた。", True),
])
def test_achievement_requires_independent_reported_outcome(monkeypatch, text, expected):
    monkeypatch.setenv("NOTES_LABEL_CACHE_DISABLE", "1")
    audit = {}
    result = label_notes_in_batches([_summary(text)], chat_completion=_positive_reply,
        outcome_chat_completion=_verifier(expected), model="test-model", audit=audit)
    assert result["2000-01-02"].achievement_flag is expected
    assert audit["source_grounding_rejections"].get("unsupported_achievement", 0) == int(not expected)
    assert audit["parse_error"] is False


def test_grounded_cache_reuse_keeps_exercise_and_does_not_repeat_ai(monkeypatch, tmp_path):
    monkeypatch.delenv("NOTES_LABEL_CACHE_DISABLE", raising=False)
    monkeypatch.setenv("NOTES_LABEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-cache-key-not-for-network")
    summary = _summary("ジムで筋トレをした。協議タスクを完了に変更したが、実施日は未確認。")
    first_audit = {}
    first = label_notes_in_batches([summary], chat_completion=_positive_reply,
        outcome_chat_completion=_verifier(False), model="test-model", audit=first_audit)
    def no_second_call(**_kwargs):
        raise AssertionError("unchanged normalized label must use cache")
    second_audit = {}
    second = label_notes_in_batches([summary], chat_completion=no_second_call,
        outcome_chat_completion=no_second_call, model="test-model", audit=second_audit)
    assert second[summary.target_date].achievement_flag is False
    assert second_audit["cache_hit_count"] == 1
    assert second_audit["api_calls"] == 0
    assert second_audit["source_grounding_rejections"]["unsupported_achievement"] == 1
    features = build_daily_feature_table([summary], second)
    assert bool(features.iloc[0]["notes_achievement_flag"]) is False
    assert bool(features.iloc[0]["notes_has_exercise"]) is True


def test_validator_failure_stays_unknown_and_is_not_a_healthy_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("NOTES_LABEL_CACHE_DISABLE", raising=False)
    monkeypatch.setenv("NOTES_LABEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-cache-key-not-for-network")
    monkeypatch.setenv("GITHUB_RUN_ID", "source-test-execution")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    summary = _summary("資料を完成させて、達成感を覚えた。")
    calls = []
    def unavailable(**_kwargs):
        calls.append(1)
        raise TimeoutError("synthetic validator unavailable")
    for attempt in (1, 1, 2):
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", str(attempt))
        audit = {}
        labels = label_notes_in_batches([summary], chat_completion=_positive_reply,
            outcome_chat_completion=unavailable, model="test-model", audit=audit)
        label = labels[summary.target_date]
        assert label.achievement_flag is False
        assert label.derived_flags["outcome_validation_failed"] is True
        assert audit["outcome_validation_status"] == "failed"
        assert audit["source_grounding_rejections"]["verification_failed"] == 1
        if len(calls) == 1 and audit["cache_hit_count"]:
            assert audit["cache_failed_hit_count"] == 1
            assert audit["outcome_validation_calls"] == 0
        assert audit["parse_error"] is False
    assert len(calls) == 2


def test_notes_persistence_retries_failed_validation_but_reuses_verified_result(monkeypatch):
    from dataclasses import replace
    from scripts import daily_job
    from scripts.note_batch_labeler import build_notes_label_persistence_payload

    monkeypatch.setenv("NOTES_LABEL_CACHE_DISABLE", "1")
    summary = _summary("資料を完成させて、達成感を覚えた。")
    def unavailable(**_kwargs):
        raise TimeoutError("synthetic validator unavailable")
    failed = label_notes_in_batches([summary], chat_completion=_positive_reply,
        outcome_chat_completion=unavailable, model=daily_job.model_for("notes"))[summary.target_date]
    persisted = replace(summary, **build_notes_label_persistence_payload(
        summary=summary, label=failed, model=daily_job.model_for("notes")))
    calls = []
    saved = []
    def chat(**kwargs):
        calls.append(kwargs["model"])
        rows = json.loads(kwargs["user_prompt"])["rows"]
        return _verifier(True)(**kwargs) if "candidate_tags" in rows[0] else _positive_reply(**kwargs)
    def save(_config, *, target_date, payload):
        assert target_date == summary.target_date
        saved.append(replace(summary, **payload))
        return {"updated": True}
    monkeypatch.setattr(daily_job, "chat_completion", chat)
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", save)
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda *_args: saved[-1])

    recovered = daily_job._ensure_notes_label_persisted(None, summary=persisted, run_id="next-run")
    assert len(calls) == 2
    assert len(saved) == 1
    verified = daily_job.load_persisted_note_label(recovered)
    assert verified.achievement_flag is True
    assert verified.derived_flags["outcome_verification_complete"] is True
    unchanged = daily_job._ensure_notes_label_persisted(None, summary=recovered, run_id="unchanged-run")
    assert unchanged is recovered
    assert len(calls) == 2
    assert len(saved) == 1


@pytest.mark.parametrize("source", ["cache", "persisted"])
def test_reused_verified_labels_keep_date_provenance_and_pass_real_quality(monkeypatch, tmp_path, source):
    from dataclasses import replace
    from scripts.note_batch_labeler import build_notes_label_persistence_payload
    from scripts.notes_quality import assess_notes_quality
    from scripts.today_advice_audit import TodayAdviceAuditLogger

    monkeypatch.delenv("NOTES_LABEL_CACHE_DISABLE", raising=False)
    monkeypatch.setenv("NOTES_LABEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-cache-key-not-for-network")
    summary = _summary("資料を完成させて、達成感を覚えた。")
    first = label_notes_in_batches([summary], chat_completion=_positive_reply,
        outcome_chat_completion=_verifier(True), model="test-model")[summary.target_date]
    if source == "persisted":
        summary = replace(summary, **build_notes_label_persistence_payload(summary=summary, label=first, model="test-model"))
    calls = []
    audit = {}
    labels = label_notes_in_batches([summary], chat_completion=lambda **_: calls.append(1),
        outcome_chat_completion=lambda **_: calls.append(1), model="test-model", audit=audit)
    result = assess_notes_quality(historical_summaries=[summary], note_labels=labels,
        note_label_audit=audit, target_date=summary.target_date,
        audit=TodayAdviceAuditLogger(target_date=summary.target_date, debug=False))
    assert calls == []
    assert audit["matched_dates_count"] == 1
    assert audit["notes_date_merge_success_rate"] == 1.0
    assert result.audit_metrics["notes_quality_used"] == "high"
    assert result.labels[summary.target_date].achievement_flag is True


def test_classifier_failure_memo_is_not_success_and_retries_next_attempt(monkeypatch, tmp_path):
    monkeypatch.delenv("NOTES_LABEL_CACHE_DISABLE", raising=False)
    monkeypatch.setenv("NOTES_LABEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-cache-key-not-for-network")
    monkeypatch.setenv("GITHUB_RUN_ID", "classifier-failure-test")
    calls = []
    def invalid(**_kwargs):
        calls.append(1)
        return "not valid JSON"
    for index, attempt in enumerate((1, 1, 2)):
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", str(attempt))
        audit = {}
        label_notes_in_batches([_summary("資料を完成させた。")], chat_completion=invalid,
            model="test-model", audit=audit)
        assert audit["parse_error"] is True
        assert audit["labeling_failed"] is True
        if index == 1:
            assert audit["api_calls"] == 0
            assert audit["cache_failed_hit_count"] == 1
            assert len(calls) == 2
    assert len(calls) == 4


@pytest.mark.parametrize("updated", [False, True])
def test_notes_persistence_does_not_report_success_without_saved_readback(monkeypatch, updated):
    from scripts import daily_job
    monkeypatch.setenv("NOTES_LABEL_CACHE_DISABLE", "1")
    summary = _summary("資料を完成させて、達成感を覚えた。")
    def chat(**kwargs):
        rows = json.loads(kwargs["user_prompt"])["rows"]
        return _verifier(True)(**kwargs) if "candidate_tags" in rows[0] else _positive_reply(**kwargs)
    monkeypatch.setattr(daily_job, "chat_completion", chat)
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *_args, **_kwargs: {"updated": updated})
    monkeypatch.setattr(daily_job, "_refresh_daily_log_summary", lambda *_args: summary)
    with pytest.raises(RuntimeError, match="notes_label_save_unconfirmed|generation_readback_mismatch"):
        daily_job._ensure_notes_label_persisted(None, summary=summary, run_id="not-saved")


def test_f_risk_retains_failed_source_state_without_using_false_outcomes(monkeypatch):
    from dataclasses import replace
    from scripts import f_risk_generator
    monkeypatch.setenv("NOTES_LABEL_CACHE_DISABLE", "1")
    histories = [replace(_summary("資料を完成させた。"), target_date=f"2000-01-{day:02d}") for day in range(1, 15)]
    monkeypatch.setattr(f_risk_generator, "_load_histories_with_bulk_fallback", lambda **_: histories)
    monkeypatch.setattr(f_risk_generator, "_hydrate_expense_f_from_expenses_db", lambda rows: rows)
    calls = []
    def chat(**kwargs):
        calls.append(kwargs["model"])
        rows = json.loads(kwargs["user_prompt"])["rows"]
        if "candidate_tags" in rows[0]:
            raise TimeoutError("synthetic source validator unavailable")
        return _positive_reply(**kwargs)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unverified outcome labels must not enter F features")
    monkeypatch.setattr(f_risk_generator, "chat_completion", chat)
    monkeypatch.setattr(f_risk_generator, "build_daily_feature_table", forbidden)
    result = f_risk_generator.generate_f_risk(daily_log_read_url="unused", bearer_token=None, target_date="2000-01-15")
    state = result.debug_summary["risk_json"]
    assert result.skip_reason == "notes_source_validation_unavailable"
    assert result.score is None and result.alert_text is None
    assert state["data_status"] == "failed"
    assert state["notes_source_validation"]["outcome_status"] == "failed"
    assert state["notes_source_validation"]["calls"] == 2
    assert len(calls) == 4  # Two classifier batches and two source checks, no prediction AI.


@pytest.mark.parametrize("failed_stage", ["notes_classification", "notes_outcome_validation"])
def test_real_advice_pipeline_records_source_failure_and_abstains(monkeypatch, failed_stage):
    from scripts import mood_advice_generator as generator
    monkeypatch.setenv("NOTES_LABEL_CACHE_DISABLE", "1")
    summary = _summary("資料を完成させた。")
    def classification(**kwargs):
        return "invalid JSON" if failed_stage == "notes_classification" else _positive_reply(**kwargs)
    def unavailable(**_kwargs):
        raise TimeoutError("synthetic validator unavailable")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("failed sources must not enter risk features")
    monkeypatch.setattr(generator, "_chat_completion", classification)
    monkeypatch.setattr(generator, "shared_chat_completion", unavailable)
    monkeypatch.setattr(generator, "build_daily_feature_table", forbidden)
    context = {"history": [summary], "historical_summaries": [summary], "today_summary": summary,
               "structured": {"high_mood_sample_count": 0, "low_mood_sample_count": 0}}
    result = generator.generate_today_advice(daily_log_read_url="unused", bearer_token=None,
        target_date=summary.target_date, generation_context=context)
    analysis = result.judgment_json["analysis_json"]
    assert analysis["final_status"] == "failed"
    assert analysis["failure_stage"] == failed_stage
    assert analysis["fallback_used"] is True
    assert analysis["risk_level"] == "unknown"
    assert "助言は控えます" in result.today_advice
