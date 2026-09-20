from publish.read_daily_log import read_daily_log


def test_read_daily_log_parses_workout_fields(monkeypatch):
    payload = {
        "found": True,
        "target_date": "2026-09-20",
        "workout_done": True,
        "workout_sessions": 1,
        "workout_gym": "インフィニティ24",
        "workout_duration_min": 62,
        "workout_sets": 18,
        "workout_volume_kg": 6420,
        "workout_calories": 410,
        "workout_exercises": "ベンチプレス、ラットプルダウン",
        "workout_summary": "1回 / 62分 / 18セット / Volume 6,420kg",
    }
    monkeypatch.setattr("publish.read_daily_log.fetch_json", lambda *_args, **_kwargs: payload)

    summary = read_daily_log(
        daily_log_read_url="https://example.com",
        target_date="2026-09-20",
        bearer_token=None,
    )

    assert summary is not None
    assert summary.workout_done is True
    assert summary.workout_sessions == 1
    assert summary.workout_gym == "インフィニティ24"
    assert summary.workout_duration_min == 62
    assert summary.workout_sets == 18
    assert summary.workout_volume_kg == 6420
    assert summary.workout_calories == 410
    assert "ベンチプレス" in (summary.workout_exercises or "")


def test_read_daily_log_preserves_no_workout(monkeypatch):
    payload = {
        "found": True,
        "target_date": "2026-09-20",
        "workout_done": False,
        "workout_sessions": 0,
        "workout_duration_min": 0,
        "workout_sets": 0,
        "workout_volume_kg": 0,
        "workout_calories": 0,
        "workout_summary": "トレーニング記録なし",
    }
    monkeypatch.setattr("publish.read_daily_log.fetch_json", lambda *_args, **_kwargs: payload)

    summary = read_daily_log(
        daily_log_read_url="https://example.com",
        target_date="2026-09-20",
        bearer_token=None,
    )

    assert summary is not None
    assert summary.workout_done is False
    assert summary.workout_sessions == 0
    assert summary.workout_sets == 0
