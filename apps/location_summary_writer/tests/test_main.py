import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from main import Config, LocationLog, compute_window, segment_logs


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
            LocationLog("1", datetime.fromisoformat("2026-02-15T06:00:00+09:00"), "A", 35.10004, 139.10004, ""),
            LocationLog("2", datetime.fromisoformat("2026-02-15T06:30:00+09:00"), "A", 35.10003, 139.10001, ""),
            LocationLog("3", datetime.fromisoformat("2026-02-15T07:00:00+09:00"), "B", 35.20001, 139.20001, ""),
        ]
        segments = segment_logs(logs, cfg, datetime.fromisoformat("2026-02-16T05:00:00+09:00"))
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].raw_count, 2)
        self.assertEqual(segments[1].place_label, "B")


if __name__ == "__main__":
    unittest.main()
