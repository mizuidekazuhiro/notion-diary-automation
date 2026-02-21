import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from env import ConfigError
from main import (
    StaySession,
    TaskEvent,
    compute_window,
    generate_location_summary,
    load_config,
    match_tasks,
    merge_sessions,
)


class MainTests(unittest.TestCase):
    def test_compute_window_uses_latest_5am(self):
        now = datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc)  # 09:00 JST
        start, end, diary_date = compute_window(now, "Asia/Tokyo", 5)
        self.assertEqual(start.isoformat(), "2026-02-15T05:00:00+09:00")
        self.assertEqual(end.isoformat(), "2026-02-16T05:00:00+09:00")
        self.assertEqual(diary_date, "2026-02-15")

    def test_merge_sessions_within_10_minutes(self):
        sessions = [
            StaySession(
                start=datetime.fromisoformat("2026-02-15T08:00:00+09:00"),
                end=datetime.fromisoformat("2026-02-15T09:00:00+09:00"),
                display_name="自宅",
                category="home",
                duration_min=60,
            ),
            StaySession(
                start=datetime.fromisoformat("2026-02-15T09:08:00+09:00"),
                end=datetime.fromisoformat("2026-02-15T10:00:00+09:00"),
                display_name="自宅",
                category="home",
                duration_min=52,
            ),
        ]

        merged = merge_sessions(sessions)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].duration_min, 120)

    def test_match_tasks_prefers_work(self):
        sessions = [
            StaySession(
                start=datetime.fromisoformat("2026-02-15T18:00:00+09:00"),
                end=datetime.fromisoformat("2026-02-15T20:00:00+09:00"),
                display_name="自宅",
                category="home",
                duration_min=120,
            ),
            StaySession(
                start=datetime.fromisoformat("2026-02-15T18:30:00+09:00"),
                end=datetime.fromisoformat("2026-02-15T19:30:00+09:00"),
                display_name="勤務先",
                category="work",
                duration_min=60,
            ),
        ]
        tasks = [TaskEvent(title="レビュー", event_time=datetime.fromisoformat("2026-02-15T19:00:00+09:00"))]

        matched = match_tasks(tasks, sessions)
        self.assertEqual(matched[0].session.display_name, "勤務先")

    def test_generate_location_summary_contains_required_sections(self):
        window_start = datetime.fromisoformat("2026-02-15T05:00:00+09:00")
        window_end = datetime.fromisoformat("2026-02-16T05:00:00+09:00")
        sessions = [
            StaySession(
                start=datetime.fromisoformat("2026-02-15T07:00:00+09:00"),
                end=datetime.fromisoformat("2026-02-15T09:00:00+09:00"),
                display_name="自宅",
                category="home",
                duration_min=120,
            )
        ]
        tasks = [TaskEvent(title="買い物", event_time=datetime.fromisoformat("2026-02-15T19:00:00+09:00"))]

        text = generate_location_summary(window_start, window_end, sessions, tasks)
        self.assertIn("要約", text)
        self.assertIn("タイムライン（事実）", text)
        self.assertIn("日記本文", text)
        self.assertIn("『買い物』", text)

    @patch.dict(
        os.environ,
        {
            "NOTION_TOKEN": "notion",
            "STAY_SESSIONS_DB_ID": "stay",
            "TASK_DB_ID": "tasks",
            "DAILY_LOG_DB_ID": "daily",
            "WINDOW_START_HOUR": "",
            "TZ": "",
            "DAILY_LOG_DATE_PROP": "",
            "DAILY_LOG_LOCATION_SUMMARY_PROP": "",
            "DRY_RUN": "",
        },
        clear=True,
    )
    def test_load_config_treats_empty_env_as_unset(self):
        cfg = load_config()
        self.assertEqual(cfg.window_start_hour, 5)
        self.assertEqual(cfg.tz, "Asia/Tokyo")
        self.assertEqual(cfg.daily_log_location_summary_prop, "Location summary")
        self.assertFalse(cfg.dry_run)

    @patch.dict(
        os.environ,
        {
            "NOTION_TOKEN": "notion",
            "STAY_SESSIONS_DB_ID": "stay",
            "TASK_DB_ID": "tasks",
            "DAILY_LOG_DB_ID": "daily",
            "WINDOW_START_HOUR": "abc",
        },
        clear=True,
    )
    def test_load_config_raises_on_invalid_int(self):
        with self.assertRaisesRegex(ConfigError, "WINDOW_START_HOUR must be int"):
            load_config()

    @patch.dict(
        os.environ,
        {
            "NOTION_TOKEN": "notion",
            "STAY_SESSIONS_DB_ID": "",
            "TASK_DB_ID": "tasks",
            "DAILY_LOG_DB_ID": "daily",
        },
        clear=True,
    )
    def test_load_config_raises_when_required_is_empty(self):
        with self.assertRaisesRegex(ConfigError, "STAY_SESSIONS_DB_ID is required"):
            load_config()


if __name__ == "__main__":
    unittest.main()
