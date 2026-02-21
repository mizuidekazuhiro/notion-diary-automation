import os
import sys
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from env import ConfigError, get_env_bool, get_env_int, get_env_str

NOTION_VERSION = "2022-06-28"
LOGGER = logging.getLogger(__name__)
UNKNOWN_WORDS = {"", "unknown", "不明"}


@dataclass
class Config:
    notion_token: str
    stay_sessions_db_id: str
    task_db_id: str
    daily_log_db_id: str

    tz: str = "Asia/Tokyo"
    window_start_hour: int = 5

    daily_log_date_prop: str = "Date"
    daily_log_location_summary_prop: str = "Location summary"

    dry_run: bool = False


@dataclass
class StaySession:
    start: datetime
    end: datetime
    display_name: str
    category: str
    duration_min: int


@dataclass
class TaskEvent:
    title: str
    event_time: datetime


@dataclass
class MatchedTask:
    task: TaskEvent
    session: StaySession | None


class NotionClient:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            }
        )

    def query_database(self, database_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        url = f"https://api.notion.com/v1/databases/{database_id}/query"
        all_results: list[dict[str, Any]] = []
        next_cursor = None

        while True:
            body = dict(payload)
            if next_cursor:
                body["start_cursor"] = next_cursor

            resp = self.session.post(url, json=body, timeout=30)
            if resp.status_code >= 400:
                raise RuntimeError(f"Notion query failed ({resp.status_code}): {resp.text}")

            data = resp.json()
            all_results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            next_cursor = data.get("next_cursor")

        return all_results

    def update_page_properties(self, page_id: str, properties: dict[str, Any]) -> None:
        url = f"https://api.notion.com/v1/pages/{page_id}"
        resp = self.session.patch(url, json={"properties": properties}, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion update failed ({resp.status_code}): {resp.text}")


def load_config() -> Config:
    return Config(
        notion_token=get_env_str("NOTION_TOKEN", required=True),
        stay_sessions_db_id=get_env_str("STAY_SESSIONS_DB_ID", required=True),
        task_db_id=get_env_str("TASK_DB_ID", required=True),
        daily_log_db_id=get_env_str("DAILY_LOG_DB_ID", required=True),
        tz=get_env_str("TZ", default="Asia/Tokyo"),
        window_start_hour=get_env_int("WINDOW_START_HOUR", default=5, min=0, max=23),
        daily_log_date_prop=get_env_str("DAILY_LOG_DATE_PROP", default="Date"),
        daily_log_location_summary_prop=get_env_str(
            "DAILY_LOG_LOCATION_SUMMARY_PROP", default="Location summary"
        ),
        dry_run=get_env_bool("DRY_RUN", default=False),
    )


def log_effective_config(cfg: Config) -> None:
    LOGGER.info(
        "config: tz=%s window_start_hour=%s dry_run=%s",
        cfg.tz,
        cfg.window_start_hour,
        cfg.dry_run,
    )
    LOGGER.info(
        "config: daily_log_date_prop=%s daily_log_location_summary_prop=%s",
        cfg.daily_log_date_prop,
        cfg.daily_log_location_summary_prop,
    )


def compute_window(now_utc: datetime, tz_name: str, window_start_hour: int) -> tuple[datetime, datetime, str]:
    tz = ZoneInfo(tz_name)
    local_now = now_utc.astimezone(tz)
    candidate_end = local_now.replace(hour=window_start_hour, minute=0, second=0, microsecond=0)
    if local_now < candidate_end:
        candidate_end -= timedelta(days=1)

    window_end = candidate_end
    window_start = window_end - timedelta(days=1)
    diary_date = (window_end.date() - timedelta(days=1)).isoformat()
    return window_start, window_end, diary_date


def parse_datetime(v: str) -> datetime:
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def rich_text_plain(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "title":
        return "".join(x.get("plain_text", "") for x in prop.get("title", []))
    if ptype == "rich_text":
        return "".join(x.get("plain_text", "") for x in prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if ptype == "status":
        st = prop.get("status")
        return st.get("name", "") if st else ""
    return ""


def parse_number(prop: dict[str, Any] | None) -> float | None:
    if not prop:
        return None
    if prop.get("type") == "number":
        return prop.get("number")
    text = rich_text_plain(prop)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_label(place_label: str, name: str) -> str:
    label = place_label.strip()
    if label and label.lower() not in UNKNOWN_WORDS:
        return label
    name = name.strip()
    if name and name.lower() not in UNKNOWN_WORDS:
        return name
    return "不明"


def normalize_category(category: str) -> str:
    c = category.strip().lower()
    if c in {"home", "work", "unknown"}:
        return c
    if c:
        return "other"
    return "unknown"


def clip_session(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> tuple[datetime, datetime] | None:
    clipped_start = max(start, window_start)
    clipped_end = min(end, window_end)
    if clipped_end <= clipped_start:
        return None
    return clipped_start, clipped_end


def parse_stay_sessions(
    pages: list[dict[str, Any]], window_start: datetime, window_end: datetime
) -> list[StaySession]:
    sessions: list[StaySession] = []
    for page in pages:
        props = page.get("properties", {})
        start_obj = props.get("SessionStart", {})
        end_obj = props.get("SessionEnd", {})
        start_date = start_obj.get("date") if start_obj.get("type") == "date" else None
        end_date = end_obj.get("date") if end_obj.get("type") == "date" else None
        if not start_date or not start_date.get("start") or not end_date or not end_date.get("start"):
            continue

        raw_duration = parse_number(props.get("DurationMin"))
        if raw_duration is None or raw_duration <= 0:
            continue

        start = parse_datetime(start_date["start"])
        end = parse_datetime(end_date["start"])
        clipped = clip_session(start, end, window_start, window_end)
        if not clipped:
            continue

        clipped_start, clipped_end = clipped
        duration_min = int((clipped_end - clipped_start).total_seconds() // 60)
        if duration_min <= 0:
            continue

        sessions.append(
            StaySession(
                start=clipped_start,
                end=clipped_end,
                display_name=normalize_label(
                    rich_text_plain(props.get("PlaceLabel")),
                    rich_text_plain(props.get("Name")),
                ),
                category=normalize_category(rich_text_plain(props.get("PlaceCategory"))),
                duration_min=duration_min,
            )
        )

    sessions.sort(key=lambda x: x.start)
    return sessions


def merge_sessions(sessions: list[StaySession], gap_minutes: int = 10) -> list[StaySession]:
    if not sessions:
        return []

    merged: list[StaySession] = [sessions[0]]
    max_gap = timedelta(minutes=gap_minutes)

    for session in sessions[1:]:
        last = merged[-1]
        if session.display_name == last.display_name and session.start - last.end <= max_gap:
            new_end = max(last.end, session.end)
            merged[-1] = StaySession(
                start=last.start,
                end=new_end,
                display_name=last.display_name,
                category=last.category if last.category != "unknown" else session.category,
                duration_min=int((new_end - last.start).total_seconds() // 60),
            )
            continue
        merged.append(session)

    return merged


def parse_tasks(pages: list[dict[str, Any]], window_start: datetime, window_end: datetime) -> list[TaskEvent]:
    tasks: list[TaskEvent] = []
    for page in pages:
        props = page.get("properties", {})
        date_prop = props.get("Event Date", {})
        date_obj = date_prop.get("date") if date_prop.get("type") == "date" else None
        if not date_obj or not date_obj.get("start"):
            continue
        event_time = parse_datetime(date_obj["start"])
        if not (window_start <= event_time <= window_end):
            continue
        title = rich_text_plain(props.get("Name")).strip() or "名称未設定"
        tasks.append(TaskEvent(title=title, event_time=event_time))

    tasks.sort(key=lambda x: x.event_time)
    return tasks


def session_priority(category: str) -> int:
    if category == "work":
        return 0
    if category not in {"unknown", "home"}:
        return 1
    if category == "home":
        return 2
    return 3


def match_tasks(tasks: list[TaskEvent], sessions: list[StaySession]) -> list[MatchedTask]:
    matched: list[MatchedTask] = []
    tolerance = timedelta(minutes=30)
    for task in tasks:
        candidates = [
            s
            for s in sessions
            if s.start - tolerance <= task.event_time <= s.end + tolerance
        ]
        candidates.sort(
            key=lambda s: (
                session_priority(s.category),
                min(abs((task.event_time - s.start).total_seconds()), abs((s.end - task.event_time).total_seconds())),
                -s.duration_min,
            )
        )
        matched.append(MatchedTask(task=task, session=candidates[0] if candidates else None))
    return matched


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def build_summary_lines(sessions: list[StaySession], tasks: list[TaskEvent]) -> str:
    if not sessions:
        task_text = "予定はなし" if not tasks else f"予定は{len(tasks)}件"
        return f"滞在セッションは確認できませんでした。{task_text}でした。"

    totals = {"home": 0, "work": 0, "other": 0, "unknown": 0}
    for s in sessions:
        key = s.category if s.category in totals else "other"
        totals[key] += s.duration_min

    total_min = sum(totals.values()) or 1

    def ratio(v: int) -> int:
        return round(v * 100 / total_min)

    task_text = "予定なし" if not tasks else f"予定{len(tasks)}件あり"
    return (
        f"滞在比率は自宅{ratio(totals['home'])}%・勤務先{ratio(totals['work'])}%・"
        f"その他{ratio(totals['other'])}%・不明{ratio(totals['unknown'])}%でした。{task_text}。"
    )


def build_timeline(sessions: list[StaySession], matched_tasks: list[MatchedTask]) -> str:
    events: list[tuple[datetime, str]] = []
    for s in sessions:
        cat = {"home": "home", "work": "work", "unknown": "unknown"}.get(s.category, "other")
        events.append(
            (
                s.start,
                f"- {_fmt_time(s.start)}–{_fmt_time(s.end)} {s.display_name}（{cat}） 滞在: {s.duration_min}分",
            )
        )

    for m in matched_tasks:
        events.append((m.task.event_time, f"- {_fmt_time(m.task.event_time)} 予定: 『{m.task.title}』"))

    events.sort(key=lambda x: x[0])
    return "\n".join(e[1] for e in events)


def _period_name(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 11:
        return "朝"
    if 11 <= h < 18:
        return "日中"
    return "夜"


def build_diary_text(sessions: list[StaySession], matched_tasks: list[MatchedTask]) -> str:
    if not sessions and not matched_tasks:
        return "この時間帯は記録が少なく、滞在情報と予定の確認ができませんでした。"

    period_facts: dict[str, list[str]] = {"朝": [], "日中": [], "夜": []}
    for s in sessions:
        fact = f"{_fmt_time(s.start)}から{_fmt_time(s.end)}までは{s.display_name}に滞在"
        if s.category == "unknown":
            fact += "（不明な短時間滞在を含む）"
        period_facts[_period_name(s.start)].append(fact)

    for m in matched_tasks:
        t = _fmt_time(m.task.event_time)
        if m.session:
            fact = (
                f"{t}に『{m.task.title}』の予定があり、"
                f"その時間帯は{m.session.display_name}に滞在していた"
            )
        else:
            fact = f"{t}に『{m.task.title}』の予定が入っていた"
        period_facts[_period_name(m.task.event_time)].append(fact)

    lines: list[str] = []
    for period in ["朝", "日中", "夜"]:
        if period_facts[period]:
            lines.append(f"{period}は、" + "。".join(period_facts[period]) + "。")

    return "\n".join(lines)


def generate_location_summary(
    window_start: datetime,
    window_end: datetime,
    sessions: list[StaySession],
    tasks: list[TaskEvent],
) -> str:
    matched_tasks = match_tasks(tasks, sessions)
    summary = build_summary_lines(sessions, tasks)
    timeline = build_timeline(sessions, matched_tasks)
    diary = build_diary_text(sessions, matched_tasks)

    parts = [
        f"要約\n{summary}",
        "タイムライン（事実）\n" + (timeline if timeline else "- 記録なし"),
        "日記本文\n" + diary,
    ]
    return "\n\n".join(parts)


def find_daily_log_page(notion: NotionClient, cfg: Config, diary_date: str) -> dict[str, Any] | None:
    query = {
        "filter": {
            "property": cfg.daily_log_date_prop,
            "date": {"equals": diary_date},
        },
        "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
        "page_size": 100,
    }
    pages = notion.query_database(cfg.daily_log_db_id, query)
    return pages[0] if pages else None


def build_summary_property(page: dict[str, Any], property_name: str, text: str) -> dict[str, Any]:
    prop = page.get("properties", {}).get(property_name)
    if not prop:
        raise KeyError(f"Daily Log property not found: {property_name}")

    ptype = prop.get("type")
    if ptype == "rich_text":
        return {property_name: {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]}}
    if ptype == "title":
        return {property_name: {"title": [{"type": "text", "text": {"content": text[:1900]}}]}}

    raise TypeError(f"Property {property_name} type is unsupported for text update: {ptype}")


def run() -> None:
    cfg = load_config()
    os.environ["TZ"] = cfg.tz
    log_effective_config(cfg)
    now = datetime.now(timezone.utc)
    window_start, window_end, diary_date = compute_window(now, cfg.tz, cfg.window_start_hour)

    print(f"Window: [{window_start.isoformat()}, {window_end.isoformat()})")
    print(f"Target diary_date: {diary_date}")

    notion = NotionClient(cfg.notion_token)

    sessions_query = {
        "filter": {
            "and": [
                {"property": "SessionStart", "date": {"on_or_before": window_end.isoformat()}},
                {"property": "SessionEnd", "date": {"on_or_after": window_start.isoformat()}},
            ]
        },
        "sorts": [{"property": "SessionStart", "direction": "ascending"}],
        "page_size": 100,
    }
    task_query = {
        "filter": {
            "and": [
                {"property": "Event Date", "date": {"on_or_after": window_start.isoformat()}},
                {"property": "Event Date", "date": {"on_or_before": window_end.isoformat()}},
            ]
        },
        "sorts": [{"property": "Event Date", "direction": "ascending"}],
        "page_size": 100,
    }

    session_pages = notion.query_database(cfg.stay_sessions_db_id, sessions_query)
    task_pages = notion.query_database(cfg.task_db_id, task_query)

    sessions = merge_sessions(parse_stay_sessions(session_pages, window_start, window_end))
    tasks = parse_tasks(task_pages, window_start, window_end)
    summary_text = generate_location_summary(window_start, window_end, sessions, tasks)
    print(f"Fetched sessions={len(sessions)} tasks={len(tasks)}")

    page = find_daily_log_page(notion, cfg, diary_date)
    if not page:
        raise RuntimeError(f"Daily Log page not found for date={diary_date}")

    if cfg.dry_run:
        print("DRY_RUN=true: Notion page update skipped")
        print("Generated summary preview:")
        print(summary_text)
        return

    patch = build_summary_property(page, cfg.daily_log_location_summary_prop, summary_text)
    notion.update_page_properties(page["id"], patch)
    print("Daily Log Location summary updated successfully")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        sys.exit(1)
