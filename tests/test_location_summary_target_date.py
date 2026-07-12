from __future__ import annotations

import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[1] / "apps" / "location_summary_writer" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from main import compute_window_for_diary_date


def test_compute_window_for_target_diary_date() -> None:
    start, end, diary_date = compute_window_for_diary_date("2026-07-08", "Asia/Tokyo", 5)
    assert diary_date == "2026-07-08"
    assert start.isoformat() == "2026-07-08T05:00:00+09:00"
    assert end.isoformat() == "2026-07-09T05:00:00+09:00"
