from __future__ import annotations

from types import SimpleNamespace

from scripts.daily_mail_quality import build_quality_report


def _summary(**overrides: object) -> SimpleNamespace:
    base = {
        "target_date": "2026-05-08",
        "today_advice": "直近7日の記録傾向と睡眠状態を踏まえると、今日は学習とタスクを午前に寄せるのがよいです。過去の高評価日は短い着手を早めに置いた日と重なります。まず一問だけ解き、次に重要タスクを一つ閉じる形に絞ってください。",
        "study_minutes": None,
        "study_sessions": None,
        "study_last_used_at": "",
        "resolved_sleep_duration_min": None,
        "sleep_duration_min": None,
        "sleep_score": None,
        "sleep_start": "",
        "sleep_end": "",
        "sleep_analysis_jp": "",
        "today_condition_forecast_jp": "",
        "weather_summary": "",
        "weather_location": "",
        "weather_code": None,
        "weather_temp_max_c": None,
        "weather_temp_min_c": None,
        "diary": "今日はテスト用の日記です。",
        "meal_summary": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _codes(report: dict[str, object]) -> set[str]:
    issues = report.get("issues")
    assert isinstance(issues, list)
    return {str(issue.get("code")) for issue in issues if isinstance(issue, dict)}


def test_flags_study_data_when_not_rendered() -> None:
    summary = _summary(study_minutes=45, study_sessions=2)
    report = build_quality_report(
        summary,
        mail_plain_text="Daily Log | 2026-05-08\nToday advice\nDiary\nWeather",
        mail_html="<html></html>",
    )

    assert "study_not_rendered" in _codes(report)
    assert report["status"] == "fail"


def test_flags_missing_today_advice() -> None:
    report = build_quality_report(
        _summary(today_advice=""),
        mail_plain_text="Daily Log | 2026-05-08\nDiary",
        mail_html="<html></html>",
    )

    assert "today_advice_missing" in _codes(report)
    assert report["status"] == "fail"


def test_passes_when_required_sections_are_present() -> None:
    summary = _summary(study_minutes=30, sleep_score=80, weather_summary="晴れ")
    report = build_quality_report(
        summary,
        mail_plain_text="Daily Log | 2026-05-08\nToday advice\n司法試験 Study\nSleep & Condition\nWeather\nDiary",
        mail_html="<html></html>",
    )

    assert report["status"] == "pass"
    assert _codes(report) == set()
