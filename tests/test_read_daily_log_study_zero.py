from publish.read_daily_log import read_daily_log


def test_read_daily_log_preserves_study_zero(monkeypatch):
    payload = {
        "found": True,
        "target_date": "2026-05-10",
        "study_minutes": 0,
        "study_sessions": 0,
        "study_last_used_at": "2026-05-10T09:00:00+09:00",
    }
    monkeypatch.setattr("publish.read_daily_log.fetch_json", lambda *_args, **_kwargs: payload)
    s = read_daily_log(daily_log_read_url="https://example.com", target_date="2026-05-10", bearer_token=None)
    assert s is not None
    assert s.study_minutes == 0
    assert s.study_sessions == 0
    assert s.study_last_used_at == "2026-05-10T09:00:00+09:00"
