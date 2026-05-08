from __future__ import annotations

from types import SimpleNamespace

from publish.email_templates import render_daily_log_html, render_daily_log_text
from publish.read_daily_log import read_daily_log
from scripts.mail_dedupe import build_mail_input_snapshot, snapshot_json, sha256_hex


def test_location_summary_gpt_only_used(monkeypatch):
    payload = {
        "found": True,
        "target_date": "2026-05-07",
        "page_id": "p1",
        "title": "Daily Log",
        "summary_text": "",
        "summary_html": "",
        "mail_id": "m1",
        "Location summary (GPT)": "渋谷と恵比寿で過ごした",
    }
    monkeypatch.setattr("publish.read_daily_log.fetch_json", lambda *_args, **_kwargs: payload)
    summary = read_daily_log(daily_log_read_url="http://dummy", target_date="2026-05-07", bearer_token=None)
    assert summary is not None
    assert summary.location_summary == "渋谷と恵比寿で過ごした"


def test_location_summary_gpt_preferred_over_legacy(monkeypatch):
    payload = {
        "found": True,
        "target_date": "2026-05-07",
        "page_id": "p1",
        "title": "Daily Log",
        "summary_text": "",
        "summary_html": "",
        "mail_id": "m1",
        "Location summary (GPT)": "GPT版",
        "Location summary": "旧版",
        "location_summary": "snake",
    }
    monkeypatch.setattr("publish.read_daily_log.fetch_json", lambda *_args, **_kwargs: payload)
    summary = read_daily_log(daily_log_read_url="http://dummy", target_date="2026-05-07", bearer_token=None)
    assert summary is not None
    assert summary.location_summary == "GPT版"


def test_meal_photos_render_as_image_or_link_and_text_urls():
    payload = {
        "target_date": "2026-05-07",
        "meal_summary": "ok",
        "location_summary": "loc",
        "meal_photos": ["https://www.dropbox.com/s/abc/photo1.jpg?dl=0", "http://invalid.local/photo2.jpg"],
    }
    html = render_daily_log_html(payload)
    text = render_daily_log_text(payload)
    assert '<img src="https://www.dropbox.com/s/abc/photo1.jpg?dl=0"' in html
    assert '<a href="http://invalid.local/photo2.jpg">' in html
    assert "- https://www.dropbox.com/s/abc/photo1.jpg?dl=0" in text


def test_mail_input_hash_changes_when_meal_photos_change():
    base = SimpleNamespace(
        target_date="2026-05-07", diary="d", today_advice="a", sleep_analysis_jp="s", today_condition_forecast_jp="c",
        weather_summary="w", weather_location="tokyo", weather_temp_max_c=25.0, weather_temp_min_c=18.0,
        weather_precip_probability_max=10.0, weather_code=1, activity_summary="act", location_summary="loc",
        meal_summary="meal", meal_photos=["https://a"], expenses_total=1000.0,
        expenses=SimpleNamespace(count=0, top=[]), done_count=0, done_tasks=[], done_tasks_detail=[], drop_count=0,
        drop_tasks=[], kcal=1.0, protein=1.0, fat=1.0, carb=1.0, weight=60.0, sleep_start=None, sleep_end=None,
        resolved_sleep_duration_min=1.0, resolved_sleep_duration_text="1分", sleep_score=1.0, sleep_source="x",
        deep_duration_min=1.0, rem_duration_min=1.0, readiness_stars=1.0, readiness_hrv=1.0, readiness_bpm=1.0,
        study_minutes=None, study_sessions=None, study_last_used_at=None,
    )
    snap1 = build_mail_input_snapshot(base, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})
    h1 = sha256_hex(snapshot_json(snap1))
    base.meal_photos = ["https://a", "https://b"]
    snap2 = build_mail_input_snapshot(base, expense_f_alert={"summary": ""}, f_risk_alert={"summary": ""})
    h2 = sha256_hex(snapshot_json(snap2))
    assert h1 != h2


def test_read_daily_log_filters_file_and_notion_internal_urls(monkeypatch):
    payload = {
        "found": True,
        "target_date": "2026-05-07",
        "page_id": "p1",
        "title": "Daily Log",
        "summary_text": "",
        "summary_html": "",
        "mail_id": "m1",
        "Meal Photos": ["file:///tmp/photo.jpg", "https://www.notion.so/image/xxx?permissionRecord=abc", "https://www.dropbox.com/s/abc/photo.jpg?dl=0"],
    }
    monkeypatch.setattr("publish.read_daily_log.fetch_json", lambda *_args, **_kwargs: payload)
    summary = read_daily_log(daily_log_read_url="http://dummy", target_date="2026-05-07", bearer_token=None)
    assert summary is not None
    assert summary.meal_photos == ["https://www.dropbox.com/s/abc/photo.jpg?raw=1"]
