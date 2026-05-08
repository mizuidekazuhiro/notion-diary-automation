from __future__ import annotations

from types import SimpleNamespace

from scripts.daily_mail_quality import build_quality_report


VALID_TODAY_ADVICE = (
    "直近7日の記録傾向と睡眠状態を踏まえると、今日は学習とタスクを午前に寄せるのがよいです。"
    "過去の高評価日は短い着手を早めに置いた日と重なり、低評価日は着手が遅れたまま記録が薄くなる傾向があります。"
    "睡眠スコアが一定程度ある日は判断を前倒ししやすいので、まず司法試験の一問だけ解き、次に重要タスクを一つ閉じる形に絞ってください。"
    "食事や支出の当日値は評価せず、過去の行動パターンだけを補助材料にして進めます。"
    "夕方以降は新規着手を増やさず、記録更新と翌日の準備に限定すると、過去の低評価日と同じ遅延パターンを避けやすくなります。"
)


def _summary(**overrides: object) -> SimpleNamespace:
    base = {
        "target_date": "2026-05-08",
        "today_advice": VALID_TODAY_ADVICE,
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
        "meal_photos": [],
        "location_summary": "",
        "location_summary_source": "empty",
        "mail_input_snapshot_json": "{\"meal_photos\":[],\"location_summary\":\"\"}",
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


def test_fail_when_location_summary_gpt_not_rendered() -> None:
    summary = _summary(location_summary="渋谷", location_summary_source="location_summary_gpt")
    report = build_quality_report(summary, mail_plain_text="Daily Log\nDiary", mail_html="<html></html>")
    assert "location_summary_not_rendered" in _codes(report)
    assert "location_summary_gpt_not_rendered" in _codes(report)


def test_fail_when_meal_photos_not_rendered() -> None:
    summary = _summary(meal_photos=["https://example.com/a.jpg"])
    report = build_quality_report(summary, mail_plain_text="Daily Log\nDiary", mail_html="<html></html>")
    assert "meal_photos_not_rendered" in _codes(report)


def test_fail_when_invalid_img_src_exists() -> None:
    summary = _summary(meal_photos=["https://example.com/a.jpg"])
    report = build_quality_report(summary, mail_plain_text="https://example.com/a.jpg", mail_html='<img src="file://abc" /><img src="https://www.notion.so/image/abc?permissionRecord=1" />')
    assert "meal_photo_invalid_img_src" in _codes(report)


def test_snapshot_fields_present_pass() -> None:
    summary = _summary(location_summary="loc", meal_photos=["https://example.com/a.jpg"], mail_input_snapshot_json='{"meal_photos":["https://example.com/a.jpg"],"location_summary":"loc"}')
    report = build_quality_report(summary, mail_plain_text="Daily Log\nToday advice\nDiary\nLocation summary: loc\n- https://example.com/a.jpg", mail_html='<img src="https://example.com/a.jpg" />')
    assert "mail_snapshot_missing_meal_photos" not in _codes(report)
    assert "mail_snapshot_missing_location_summary" not in _codes(report)
