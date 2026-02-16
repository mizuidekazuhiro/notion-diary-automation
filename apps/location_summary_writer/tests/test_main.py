import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from env import ConfigError
from main import Config, LocationLog, compute_window, load_config, segment_logs


class MainTests(unittest.TestCase):
    def test_compute_window_uses_latest_5am(self):
        now = datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc)  # 09:00 JST
        start, end, diary_date = compute_window(now, "Asia/Tokyo", 5)
        self.assertEqual(start.isoformat(), "2026-02-15T05:00:00+09:00")
        self.assertEqual(end.isoformat(), "2026-02-16T05:00:00+09:00")
        self.assertEqual(diary_date, "2026-02-15")

    def test_segment_logs_groups_rounded_lat_lon(self):
        cfg = Config(
            notion_token="x",
            location_log_db_id="x",
            daily_log_db_id="x",
            openai_api_key="x",
        )
        logs = [
            LocationLog("1", datetime.fromisoformat("2026-02-15T06:00:00+09:00"), "A", 35.10004, 139.10004),
            LocationLog("2", datetime.fromisoformat("2026-02-15T06:30:00+09:00"), "A", 35.10003, 139.10001),
            LocationLog("3", datetime.fromisoformat("2026-02-15T07:00:00+09:00"), "B", 35.20001, 139.20001),
        ]
        segments = segment_logs(logs, cfg, datetime.fromisoformat("2026-02-16T05:00:00+09:00"))
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].raw_count, 2)
        self.assertEqual(segments[1].place_label, "B")

    @patch.dict(
        os.environ,
        {
            "NOTION_TOKEN": "notion",
            "LOCATION_LOG_DB_ID": "location",
            "DAILY_LOG_DB_ID": "daily",
            "OPENAI_API_KEY": "openai",
            "WINDOW_START_HOUR": "",
            "TIME_BUCKET_MINUTES": "",
            "LOCATION_ROUND_DECIMALS": "",
            "TZ": "",
            "DAILY_LOG_DATE_PROP": "",
            "DAILY_LOG_LOCATION_SUMMARY_PROP": "",
            "LOCATION_LOG_TIME_PROP": "",
            "LOCATION_LOG_PLACE_PROP": "",
            "LOCATION_LOG_LAT_PROP": "",
            "LOCATION_LOG_LON_PROP": "",
            "OPENAI_MODEL": "",
            "OPENAI_BASE_URL": "",
            "DRY_RUN": "",
        },
        clear=True,
    )
    def test_load_config_treats_empty_env_as_unset(self):
        cfg = load_config()
        self.assertEqual(cfg.window_start_hour, 5)
        self.assertEqual(cfg.time_bucket_minutes, 30)
        self.assertEqual(cfg.location_round_decimals, 4)
        self.assertEqual(cfg.tz, "Asia/Tokyo")
        self.assertEqual(cfg.daily_log_location_summary_prop, "Location summary")
        self.assertEqual(cfg.location_log_lat_prop, "Latitude (raw)")
        self.assertEqual(cfg.openai_model, "gpt-4.1-mini")
        self.assertEqual(cfg.openai_base_url, "https://api.openai.com/v1")
        self.assertFalse(cfg.dry_run)

    @patch.dict(
        os.environ,
        {
            "NOTION_TOKEN": "notion",
            "LOCATION_LOG_DB_ID": "location",
            "DAILY_LOG_DB_ID": "daily",
            "OPENAI_API_KEY": "openai",
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
            "LOCATION_LOG_DB_ID": "location",
            "DAILY_LOG_DB_ID": "daily",
            "OPENAI_API_KEY": "",
        },
        clear=True,
    )
    def test_load_config_raises_when_required_is_empty(self):
        with self.assertRaisesRegex(ConfigError, "OPENAI_API_KEY is required"):
            load_config()


if __name__ == "__main__":
    unittest.main()
