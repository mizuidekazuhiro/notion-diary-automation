from __future__ import annotations

from types import SimpleNamespace

from publish.read_daily_log import DailyLogSummary, ExpenseSummary, read_daily_log
from publish.render_mail import render_mail
from scripts import daily_job, f_risk_generator
from scripts.f_risk_generator import FRiskResult
from scripts.f_risk_state_store import FRiskStateStore


def _summary(**overrides: object) -> DailyLogSummary:
    payload = dict(
        target_date="2026-03-20",
        date="2026-03-20",
        target_date_value="2026-03-20",
        page_id="page",
        title="Daily Log",
        summary_text="",
        summary_html="",
        mail_id="run",
        source="automation",
        diary=None,
        meal_summary=None,
        meal_photos=[],
        place=None,
        activity_summary=None,
        done_count=0,
        done_tasks=[],
        done_tasks_detail=[],
        drop_count=0,
        drop_tasks=[],
        kcal=None,
        protein=None,
        fat=None,
        carb=None,
        expenses_total=1200,
        expenses=ExpenseSummary(total=1200, count=1, top=[], remaining=0),
        location_summary="自宅中心",
        mood="★★★",
        notes="メモ",
        weight=None,
        sleep_start="2026-03-19T23:30:00+09:00",
        sleep_end="2026-03-20T07:00:00+09:00",
        sleep_duration_min=450,
        resolved_sleep_duration_min=450,
        resolved_sleep_duration_hours=7.5,
        resolved_sleep_duration_text="7時間30分",
        sleep_duration_source="derived_from_start_end",
        sleep_score=80,
        sleep_source="AutoSleep",
        readiness_stars=None,
        readiness_hrv=None,
        readiness_bpm=None,
        baseline_hrv=None,
        baseline_waking_bpm=None,
        sleep_heart_rate=None,
        deep_duration_min=None,
        rem_duration_min=None,
        sleep_analysis_jp=None,
        today_condition_forecast_jp=None,
        today_advice=None,
        diary_input_hash=None,
        today_advice_input_hash=None,
        diary_generated_at=None,
        today_advice_generated_at=None,
        page_url="https://example.com/page",
        diary_notification_sent=None,
    )
    payload.update(overrides)
    return DailyLogSummary(**payload)


def test_f_risk_runtime_never_writes_notion_fields(monkeypatch) -> None:
    summary = _summary()
    config = SimpleNamespace(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    monkeypatch.setattr(daily_job, "_save_daily_log_fields", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")))
    monkeypatch.setattr(
        daily_job,
        "generate_f_risk",
        lambda **kwargs: FRiskResult(
            alert_text="alert",
            score=0.9,
            reason="ok",
            matched_patterns=["p1"],
            skip_reason=None,
            debug_summary={"risk_json": {"no_alert_reason": None}},
        ),
    )
    monkeypatch.setattr(daily_job, "aggregate_daily_expense_f", lambda *_: SimpleNamespace(count=1, total=1000, data_status="ok"))
    monkeypatch.setattr(daily_job.FRiskStateStore, "get_for_date", lambda self, *_: {})
    monkeypatch.setattr(daily_job.FRiskStateStore, "save_for_date", lambda self, *_: True)

    payload = daily_job._compute_f_risk_alert_runtime(config, summary=summary, run_id="run")

    assert payload["matched"] is True


def test_f_risk_runtime_reuses_previous_state_when_hash_unchanged(monkeypatch) -> None:
    summary = _summary()
    config = SimpleNamespace(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    monkeypatch.setattr(daily_job, "aggregate_daily_expense_f", lambda *_: SimpleNamespace(count=1, total=1000, data_status="ok"))
    monkeypatch.setattr(
        daily_job.FRiskStateStore,
        "get_for_date",
        lambda self, *_: {
            "input_hash": "reuse-hash",
            "alert_text": "cached alert",
            "score": 0.5,
                "reason": "cached",
                "matched_patterns": ["p1"],
                "no_alert_reason": None,
                "data_status": "ok",
            },
    )
    monkeypatch.setattr(daily_job, "_build_input_hash", lambda *_: ("reuse-hash", {}, ""))
    monkeypatch.setattr(
        daily_job,
        "generate_f_risk",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generate_f_risk must not be called")),
    )

    payload = daily_job._compute_f_risk_alert_runtime(config, summary=summary, run_id="run")

    assert payload["matched"] is True
    assert payload["alert_text"] == "cached alert"
    assert payload["reason"] == "cached"
    assert payload["state_meta"]["reused_previous_state"] is True


def test_f_risk_runtime_soft_fail_continues_when_generate_fails(monkeypatch) -> None:
    monkeypatch.setenv("F_RISK_SOFT_FAIL", "true")
    summary = _summary()
    config = SimpleNamespace(daily_log_read_url="read", bearer_token=None, diary_generate_url="gen")
    monkeypatch.setattr(daily_job, "aggregate_daily_expense_f", lambda *_: SimpleNamespace(count=1, total=1000, data_status="ok"))
    monkeypatch.setattr(daily_job.FRiskStateStore, "get_for_date", lambda self, *_: {})
    monkeypatch.setattr(daily_job, "_build_input_hash", lambda *_: ("new-hash", {}, ""))
    monkeypatch.setattr(daily_job, "generate_f_risk", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    payload = daily_job._compute_f_risk_alert_runtime(config, summary=summary, run_id="run")

    assert payload["matched"] is False
    assert payload["skip_reason"] == "f_risk_exception"
    assert payload["reason"] == "f_risk_failed_soft"


def test_read_daily_log_keeps_backward_compat_with_old_f_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        "publish.read_daily_log.fetch_json",
        lambda *_args, **_kwargs: {
            "found": True,
            "target_date": "2026-03-20",
            "page_id": "p",
            "title": "t",
            "summary_text": "",
            "summary_html": "",
            "mail_id": "m",
            "expenses": {},
            "Expense F Count": 2,
            "F Risk Input Hash": "abc",
        },
    )
    summary = read_daily_log(daily_log_read_url="read", target_date="2026-03-20", bearer_token=None)
    assert summary is not None
    assert summary.expense_f_count == 2
    assert summary.f_risk_input_hash == "abc"


def test_hydrate_histories_with_expenses_db_labels(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setenv("EXPENSES_DB_ID", "test-db")
    items = [_summary(target_date="2026-03-19"), _summary(target_date="2026-03-20")]
    monkeypatch.setattr(
        f_risk_generator,
        "aggregate_expense_f_for_dates",
        lambda _dates: {
            "2026-03-19": SimpleNamespace(count=0, total=0, merchants=[], categories=[], first_time=None, last_time=None, data_status="ok"),
            "2026-03-20": SimpleNamespace(count=2, total=1500, merchants=["A"], categories=["C"], first_time=None, last_time=None, data_status="ok"),
        },
    )
    hydrated = f_risk_generator._hydrate_expense_f_from_expenses_db(items)
    assert hydrated[0].expense_f_count == 0
    assert hydrated[1].expense_f_count == 2


def test_f_risk_state_store_local_fallback_read_write(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.chdir(tmp_path)
    store = FRiskStateStore()
    ok = store.save_for_date("2026-03-20", {"input_hash": "h1", "reason": "r1", "generated_at": "t1"})
    row = store.get_for_date("2026-03-20")
    assert ok is True
    assert row["input_hash"] == "h1"


def test_mail_hides_f_risk_section_when_not_matched(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("MAIL_LINK_SECRET", "secret")
    mail = render_mail(
        _summary(),
        expense_f_alert={"matched": False},
        f_risk_alert={"matched": False, "alert_text": "hidden"},
    )
    assert "F Risk Alert" not in mail.plain_text
    assert "F Risk Alert" not in mail.html_body


def test_mail_shows_f_risk_section_when_matched(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("MAIL_LINK_SECRET", "secret")
    mail = render_mail(
        _summary(),
        expense_f_alert={"matched": False},
        f_risk_alert={"matched": True, "alert_text": "visible", "score": 0.8, "matched_patterns": ["p1"], "reason": "r"},
    )
    assert "F Risk Alert" in mail.plain_text
    assert "visible" in mail.plain_text


def test_mail_hides_expense_f_section_even_when_matched(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("MAIL_LINK_SECRET", "secret")
    mail = render_mail(
        _summary(),
        expense_f_alert={"matched": True, "title": "望ましくない支出（Fプロパティ）", "summary": "昨日F支出を検知"},
        f_risk_alert={"matched": False, "alert_text": ""},
    )
    assert "望ましくない支出" not in mail.plain_text
    assert "Fプロパティ" not in mail.plain_text
    assert "望ましくない支出" not in mail.html_body
    assert "Fプロパティ" not in mail.html_body


def test_mail_weather_uses_human_summary_fallback_when_raw_weather_exists(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("MAIL_LINK_SECRET", "secret")
    mail = render_mail(
        _summary(
            weather_summary=None,
            weather_code=61,
            weather_temp_max_c=17.4,
            weather_temp_min_c=8.9,
            weather_precip_probability_max=None,
        ),
        expense_f_alert={"matched": False},
        f_risk_alert={"matched": False},
    )
    assert "弱い雨。最高17.4℃、最低8.9℃です。" in mail.plain_text
    assert "weather_code" not in mail.plain_text
    assert "弱い雨。最高17.4℃、最低8.9℃です。" in mail.html_body
