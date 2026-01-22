from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class Task:
    status: str
    done_date: str | None = None
    drop_date: str | None = None


def build_jst_range(target_date: str) -> tuple[str, str]:
    start = f"{target_date}T00:00:00+09:00"
    next_date = datetime.fromisoformat(start).astimezone(JST) + timedelta(days=1)
    end = next_date.strftime("%Y-%m-%dT00:00:00+09:00")
    return start, end


def filter_done(tasks: list[Task], start: str, end: str) -> list[Task]:
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    filtered: list[Task] = []
    for task in tasks:
        if task.status != "Done" or not task.done_date:
            continue
        done_dt = datetime.fromisoformat(task.done_date)
        if start_dt <= done_dt < end_dt:
            filtered.append(task)
    return filtered


def filter_drop(tasks: list[Task], start: str, end: str) -> list[Task]:
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    filtered: list[Task] = []
    for task in tasks:
        if task.status != "Drop" or not task.drop_date:
            continue
        drop_dt = datetime.fromisoformat(task.drop_date)
        if start_dt <= drop_dt < end_dt:
            filtered.append(task)
    return filtered


def main() -> None:
    target_date = "2026-01-23"
    start_jst, end_jst = build_jst_range("2026-01-22")
    assert start_jst == "2026-01-22T00:00:00+09:00"
    assert end_jst == "2026-01-23T00:00:00+09:00"

    tasks = [
        Task(status="Done", done_date="2026-01-22T09:00:00+09:00"),
        Task(status="Done", done_date="2026-01-16T09:00:00+09:00"),
        Task(status="Done", done_date=None),
        Task(status="Drop", drop_date="2026-01-22T10:00:00+09:00"),
        Task(status="Drop", drop_date=None),
    ]

    done = filter_done(tasks, start_jst, end_jst)
    drop = filter_drop(tasks, start_jst, end_jst)

    assert len(done) == 1, "Only 2026-01-22 Done should be included."
    assert len(drop) == 1, "Only 2026-01-22 Drop should be included."
    assert done[0].done_date == "2026-01-22T09:00:00+09:00"
    assert drop[0].drop_date == "2026-01-22T10:00:00+09:00"

    print(
        "OK: target_date=2026-01-23 range=2026-01-22T00:00:00+09:00..2026-01-23T00:00:00+09:00"
    )


if __name__ == "__main__":
    main()
