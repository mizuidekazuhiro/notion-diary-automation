from types import SimpleNamespace

from scripts.daily_job_phase_c import PhaseCDeps, run_phase_c


def _deps(logs, *, diary_text="x", fail_today=False):
    summary = SimpleNamespace(target_date="2026-03-20", diary=diary_text)

    def _refresh(_c, _d):
        return summary

    def _ok(s):
        return s

    def _today(s):
        if fail_today:
            raise RuntimeError("boom")
        return s

    def _notify(_s):
        return False

    class _L:
        def info(self, msg, *args, **kwargs):
            if msg.startswith("phase_c_step_summary"):
                logs.append(args[-1])

        def exception(self, *args, **kwargs):
            return None

    return PhaseCDeps(
        refresh_summary=_refresh,
        run_weather=_ok,
        run_expense_f=lambda _s: {"matched": False},
        run_sleep=_ok,
        run_notes_label=_ok,
        run_f_risk=_ok,
        run_today_advice=_today,
        run_diary=_ok,
        run_notify=_notify,
        mark_notified=lambda _d: None,
    ), _L()


def test_phase_c_no_pending_on_notify_skip(monkeypatch):
    logs = []
    deps, logger = _deps(logs, diary_text="")
    monkeypatch.setattr("scripts.daily_job_phase_c.logging", logger)
    run_phase_c(SimpleNamespace(), target_date="2026-03-20", run_id="r", deps=deps)
    assert logs
    assert "pending" not in set(logs[-1].values())


def test_phase_c_no_pending_on_today_advice_exception(monkeypatch):
    logs = []
    deps, logger = _deps(logs, fail_today=True)
    monkeypatch.setattr("scripts.daily_job_phase_c.logging", logger)
    try:
        run_phase_c(SimpleNamespace(), target_date="2026-03-20", run_id="r", deps=deps)
    except RuntimeError:
        pass
    assert logs
    assert "pending" not in set(logs[-1].values())
    assert logs[-1]["today_advice"] == "failed"


def test_phase_c_marks_query_failed_as_degraded(monkeypatch):
    logs = []
    deps, logger = _deps(logs)
    object.__setattr__(deps, "run_expense_f", lambda _s: {"matched": False, "data_status": "query_failed"})
    monkeypatch.setattr("scripts.daily_job_phase_c.logging", logger)
    run_phase_c(SimpleNamespace(), target_date="2026-03-20", run_id="r", deps=deps)
    assert logs[-1]["expense_f"] == "degraded"
