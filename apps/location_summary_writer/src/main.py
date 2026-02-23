import os
import sys
import logging
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from env import ConfigError, get_env_bool, get_env_int, get_env_str

NOTION_VERSION = "2022-06-28"
LOGGER = logging.getLogger(__name__)
UNKNOWN_WORDS = {"", "unknown", "不明"}
MIN_MOVEMENT_DURATION_MIN = 5


@dataclass
class Config:
    notion_token: str
    stay_sessions_db_id: str
    task_db_id: str
    daily_log_db_id: str
    expenses_db_id: str = ""

    task_event_date_prop: str = "Event Date"
    task_status_prop: str = "Status"
    task_title_prop: str = ""

    expense_date_prop: str = "Date"
    expense_received_at_prop: str = "Received At"
    expense_merchant_prop: str = "Merchant"
    expense_amount_prop: str = "Amount"
    expense_currency_prop: str = "Currency"
    expense_card_prop: str = "Card"
    expense_source_prop: str = "Source"
    expense_status_prop: str = "Status"
    expense_include_keywords: str = "焼肉,レストラン,居酒屋,カフェ,バー,食,ANA Pay"

    tz: str = "Asia/Tokyo"
    window_start_hour: int = 5

    daily_log_date_prop: str = "Date"
    daily_log_location_summary_prop: str = "Location summary"
    daily_log_gpt_location_summary_prop: str = "Location summary (GPT)"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    prompt_db_id: str = ""
    prompt_target: str = "location_summary_writer"
    prompt_variant: str = "default"
    prompt_language: str = "ja"
    prompt_time_style: str = ""

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
    status: str = ""


@dataclass
class MatchedTask:
    task: TaskEvent
    session: StaySession | None


@dataclass
class MovementSegment:
    start: datetime
    end: datetime
    display_name: str


@dataclass
class ExpenseEvent:
    merchant: str
    event_time: datetime | None
    display_time: str
    amount: float | None
    currency: str
    card: str
    source: str
    status: str
    is_keyword_match: bool


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
        expenses_db_id=get_env_str("EXPENSES_DB_ID", default=""),
        task_event_date_prop=get_env_str("TASK_EVENT_DATE_PROP", default="Event Date"),
        task_status_prop=get_env_str("TASK_STATUS_PROP", default="Status"),
        task_title_prop=get_env_str("TASK_TITLE_PROP", default=""),
        expense_date_prop=get_env_str("EXPENSE_DATE_PROP", default="Date"),
        expense_received_at_prop=get_env_str("EXPENSE_RECEIVED_AT_PROP", default="Received At"),
        expense_merchant_prop=get_env_str("EXPENSE_MERCHANT_PROP", default="Merchant"),
        expense_amount_prop=get_env_str("EXPENSE_AMOUNT_PROP", default="Amount"),
        expense_currency_prop=get_env_str("EXPENSE_CURRENCY_PROP", default="Currency"),
        expense_card_prop=get_env_str("EXPENSE_CARD_PROP", default="Card"),
        expense_source_prop=get_env_str("EXPENSE_SOURCE_PROP", default="Source"),
        expense_status_prop=get_env_str("EXPENSE_STATUS_PROP", default="Status"),
        expense_include_keywords=get_env_str(
            "EXPENSE_INCLUDE_KEYWORDS", default="焼肉,レストラン,居酒屋,カフェ,バー,食,ANA Pay"
        ),
        tz=get_env_str("TZ", default="Asia/Tokyo"),
        window_start_hour=get_env_int("WINDOW_START_HOUR", default=5, min=0, max=23),
        daily_log_date_prop=get_env_str("DAILY_LOG_DATE_PROP", default="Date"),
        daily_log_location_summary_prop=get_env_str(
            "DAILY_LOG_LOCATION_SUMMARY_PROP", default="Location summary"
        ),
        daily_log_gpt_location_summary_prop=get_env_str(
            "DAILY_LOG_GPT_LOCATION_SUMMARY_PROP", default="Location summary (GPT)"
        ),
        openai_api_key=get_env_str("OPENAI_API_KEY", default=""),
        openai_model=get_env_str("OPENAI_MODEL", default="gpt-4o-mini"),
        prompt_db_id=get_env_str("PROMPT_DB_ID", default=""),
        prompt_target=get_env_str("PROMPT_TARGET", default="location_summary_writer"),
        prompt_variant=get_env_str("PROMPT_VARIANT", default="default"),
        prompt_language=get_env_str("PROMPT_LANGUAGE", default="ja"),
        prompt_time_style=get_env_str("PROMPT_TIME_STYLE", default=""),
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
        "config: daily_log_date_prop=%s daily_log_location_summary_prop=%s daily_log_gpt_location_summary_prop=%s",
        cfg.daily_log_date_prop,
        cfg.daily_log_location_summary_prop,
        cfg.daily_log_gpt_location_summary_prop,
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
    return parse_tasks_with_props(
        pages,
        window_start,
        window_end,
        event_date_prop="Event Date",
        status_prop="Status",
        title_prop="",
    )


def parse_tasks_with_props(
    pages: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
    event_date_prop: str,
    status_prop: str,
    title_prop: str,
) -> list[TaskEvent]:
    tasks: list[TaskEvent] = []
    for page in pages:
        props = page.get("properties", {})
        date_prop = props.get(event_date_prop, {})
        date_obj = date_prop.get("date") if date_prop.get("type") == "date" else None
        if not date_obj or not date_obj.get("start"):
            continue
        event_time = parse_datetime(date_obj["start"])
        if not (window_start <= event_time < window_end):
            continue
        title = extract_task_title(props, title_prop)
        status = rich_text_plain(props.get(status_prop, {}))
        tasks.append(TaskEvent(title=title, event_time=event_time, status=status))

    tasks.sort(key=lambda x: x.event_time)
    return tasks


def session_priority(category: str) -> int:
    if category == "work":
        return 0
    if category == "home":
        return 1
    if category == "other":
        return 2
    if category not in {"unknown", "home", "other"}:
        return 2
    return 3


def match_tasks(tasks: list[TaskEvent], sessions: list[StaySession]) -> list[MatchedTask]:
    matched: list[MatchedTask] = []
    tolerance = timedelta(hours=1)
    for task in tasks:
        candidates = [
            s
            for s in sessions
            if s.start - tolerance <= task.event_time <= s.end + tolerance
        ]

        def in_range(session: StaySession) -> int:
            return 0 if session.start <= task.event_time <= session.end else 1

        def distance_to_session(session: StaySession) -> float:
            if session.start <= task.event_time <= session.end:
                return 0
            return min(
                abs((task.event_time - session.start).total_seconds()),
                abs((session.end - task.event_time).total_seconds()),
            )

        candidates.sort(
            key=lambda s: (
                in_range(s),
                distance_to_session(s),
                session_priority(s.category),
            )
        )
        matched.append(MatchedTask(task=task, session=candidates[0] if candidates else None))
    return matched


def extract_task_title(props: dict[str, Any], title_prop: str = "") -> str:
    if title_prop:
        title = rich_text_plain(props.get(title_prop, {})).strip()
        if title:
            return title
    for value in props.values():
        if isinstance(value, dict) and value.get("type") == "title":
            title = rich_text_plain(value).strip()
            if title:
                return title
    return "名称未設定"


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def is_location_informative_merchant(merchant: str) -> bool:
    m = (merchant or "").strip().lower()
    if not m:
        return False

    # 場所推定に使いにくいオンライン/課金系
    blocked_keywords = [
        "apple.com", "apple com", "bill",
        "google", "amazon", "rakuten",
        "netflix", "spotify",
        "paypal", "stripe",
        "subscription", "subscrip",
    ]
    if any(kw in m for kw in blocked_keywords):
        return False

    return True

def parse_expenses(
    pages: list[dict[str, Any]],
    cfg: Config,
    window_start: datetime,
    window_end: datetime,
) -> list[ExpenseEvent]:
    keywords = [kw.strip().lower() for kw in cfg.expense_include_keywords.split(",") if kw.strip()]
    items: list[ExpenseEvent] = []
    for page in pages:
        props = page.get("properties", {})
        received_at_prop = props.get(cfg.expense_received_at_prop, {})
        date_prop = props.get(cfg.expense_date_prop, {})

        event_time: datetime | None = None
        display_time = "（時刻不明）"

        received_at_obj = received_at_prop.get("date") if received_at_prop.get("type") == "date" else None
        date_obj = date_prop.get("date") if date_prop.get("type") == "date" else None

        if received_at_obj and received_at_obj.get("start"):
            event_time = parse_datetime(received_at_obj["start"])
            if not (window_start <= event_time < window_end):
                continue
            display_time = _fmt_time(event_time)
        elif date_obj and date_obj.get("start"):
            expense_date = date_obj["start"][:10]
            start_date = window_start.astimezone(ZoneInfo(cfg.tz)).date().isoformat()
            end_date = window_end.astimezone(ZoneInfo(cfg.tz)).date().isoformat()
            if not (start_date <= expense_date < end_date):
                continue
        else:
            continue

        merchant = rich_text_plain(props.get(cfg.expense_merchant_prop, {})).strip() or extract_task_title(props)
        # 場所推定に使えない加盟店は除外
        if not is_location_informative_merchant(merchant):
            continue
        amount = parse_number(props.get(cfg.expense_amount_prop, {}))
        currency = rich_text_plain(props.get(cfg.expense_currency_prop, {})).strip()
        card = rich_text_plain(props.get(cfg.expense_card_prop, {})).strip()
        source = rich_text_plain(props.get(cfg.expense_source_prop, {})).strip()
        status = rich_text_plain(props.get(cfg.expense_status_prop, {})).strip()
        searchable = " ".join([merchant, extract_task_title(props)]).lower()
        items.append(
            ExpenseEvent(
                merchant=merchant or "名称未設定",
                event_time=event_time,
                display_time=display_time,
                amount=amount,
                currency=currency,
                card=card,
                source=source,
                status=status,
                is_keyword_match=any(kw in searchable for kw in keywords),
            )
        )

    items.sort(key=lambda x: (0 if x.is_keyword_match else 1, x.event_time or datetime.max.replace(tzinfo=timezone.utc)))
    return items


def partition_sessions(sessions: list[StaySession], min_duration_min: int = 30) -> tuple[list[StaySession], list[StaySession]]:
    main_sessions = [s for s in sessions if s.duration_min >= min_duration_min]
    short_sessions = [s for s in sessions if s.duration_min < min_duration_min]
    return main_sessions, short_sessions

def is_unknown_label(label: str) -> bool:
    return label.strip().lower() in UNKNOWN_WORDS

def classify_movement_segments(short_sessions: list[StaySession], main_sessions: list[StaySession]) -> list[MovementSegment]:
    movement_segments: list[MovementSegment] = []
    for idx, session in enumerate(short_sessions):
        if session.duration_min < MIN_MOVEMENT_DURATION_MIN:
            continue
        has_station = "駅" in session.display_name or "station" in session.display_name.lower()
        unknown_chain = False
        if is_unknown_label(session.display_name):
            prev_unknown = idx > 0 and is_unknown_label(short_sessions[idx - 1].display_name)
            next_unknown = idx + 1 < len(short_sessions) and is_unknown_label(short_sessions[idx + 1].display_name)
            unknown_chain = prev_unknown or next_unknown
        between_main = any(
            left.end <= session.start and session.end <= right.start
            for left, right in zip(main_sessions, main_sessions[1:])
            if left.duration_min >= 30 and right.duration_min >= 30
        )
        if has_station or unknown_chain or between_main:
            movement_segments.append(
                MovementSegment(start=session.start, end=session.end, display_name=session.display_name)
            )
    return movement_segments

def _window_label(window_start: datetime, window_end: datetime, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    ws = window_start.astimezone(tz)
    we = window_end.astimezone(tz)
    return f"{ws.strftime('%Y-%m-%d %H:%M')} - {we.strftime('%Y-%m-%d %H:%M')} JST"


def build_gpt_payload(
    window_start: datetime,
    window_end: datetime,
    tz_name: str,
    main_sessions: list[StaySession],
    movement_segments: list[MovementSegment],
    matched_tasks: list[MatchedTask],
    expenses: list[ExpenseEvent],
) -> dict[str, Any]:
    return {
        "date_window": _window_label(window_start, window_end, tz_name),
        "main_sessions": [
            {
                "start": _fmt_time(s.start),
                "end": _fmt_time(s.end),
                "location": s.display_name,
                "category": s.category,
                "duration_min": s.duration_min,
            }
            for s in main_sessions
        ],
        "movement_segments": [
            {
                "time": f"{_fmt_time(m.start)}-{_fmt_time(m.end)}",
                "location": m.display_name,
            }
            for m in movement_segments
            if int((m.end - m.start).total_seconds() // 60) >= MIN_MOVEMENT_DURATION_MIN
        ],
        "events": [
            {
                "time": _fmt_time(m.task.event_time),
                "title": m.task.title,
                "status": m.task.status,
                "nearby_location": m.session.display_name if m.session else None,
            }
            for m in matched_tasks
        ],
        "expenses": [
            {
                "time": e.display_time,
                "merchant": e.merchant,
                "keyword_match": e.is_keyword_match,
            }
            for e in expenses
        ],
    }


def build_prompt_db_query(cfg: Config, prompt_type: str) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [
        {"property": "Target", "select": {"equals": cfg.prompt_target}},
        {"property": "Prompt Type", "select": {"equals": prompt_type}},
        {"property": "Approved", "checkbox": {"equals": True}},
        {"property": "Is Active", "checkbox": {"equals": True}},
    ]
    if cfg.prompt_language:
        filters.append({"property": "Language", "select": {"equals": cfg.prompt_language}})
    if cfg.prompt_variant:
        filters.append({"property": "Variant", "select": {"equals": cfg.prompt_variant}})
    if cfg.prompt_time_style:
        filters.append({"property": "Time Style", "select": {"equals": cfg.prompt_time_style}})

    return {
        "filter": {"and": filters},
        "sorts": [
            {"property": "Priority", "direction": "descending"},
            {"timestamp": "last_edited_time", "direction": "descending"},
        ],
        "page_size": 10,
    }


def fetch_prompt_from_notion(notion: NotionClient, cfg: Config, prompt_type: str) -> str | None:
    if not cfg.prompt_db_id:
        return None

    try:
        query = build_prompt_db_query(cfg, prompt_type)
        pages = notion.query_database(cfg.prompt_db_id, query)
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("prompt_fetch: type=%s source=fallback reason=notion_error error=%s", prompt_type, type(exc).__name__)
        return None

    LOGGER.info(
        "prompt_fetch: type=%s candidates=%s target=%s variant=%s language=%s time_style=%s",
        prompt_type,
        len(pages),
        cfg.prompt_target,
        cfg.prompt_variant,
        cfg.prompt_language,
        cfg.prompt_time_style or "(none)",
    )
    if len(pages) > 1:
        LOGGER.info("prompt_fetch: type=%s selection_rule=highest_priority_then_latest_edited", prompt_type)

    for idx, page in enumerate(pages):
        text = rich_text_plain(page.get("properties", {}).get("Content"))
        if text.strip():
            LOGGER.info("prompt_fetch: type=%s source=notion selected_index=%s", prompt_type, idx)
            return text

    LOGGER.info("prompt_fetch: type=%s source=fallback reason=no_content", prompt_type)
    return None


def generate_diary_from_gpt(payload: dict[str, Any], api_key: str, model: str, notion: NotionClient, cfg: Config) -> str:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for GPT diary generation")

    system_prompt = (
        "あなたは行動ログを自然な日本語の日記文に整えるアシスタントです。\n"
        "以下を厳守してください：\n"
        "1. 1行目の先頭は必ず YYYY-MM-DD で始める。\n"
        "2. 出力は日本語の自然文のみで、箇条書きは禁止。\n"
        "3. 行頭に '-', '•', '・' を使わない。\n"
        "4. 滞在(main_sessions)を主軸に、場所と時間を中心に記述する。\n"
        "5. 最大6文以内に収める。\n"
        "6. 予定は『◯◯の予定があった』の表現のみ許可する。\n"
        "7. expenses は場所推定の補助情報としてのみ使い、金額には触れない。\n"
        "8. expenses を文に入れる場合は、『◯◯に立ち寄った可能性がある』『◯◯で過ごしていたことがうかがえる』『◯◯にいたとみられる』など、自然な推定表現を使う。\n"
        "9. 『支出記録』『記録がある』『補助情報として確認できる』のような説明調の表現は使わない。\n"
        "10. オンライン決済・サブスク等の場所と関係ない情報は無理に書かない。\n"
        "11. 住所や場所が細かく揺れる場合は、近接エリアとして簡略化してよい。"
    )
    user_prompt = (
        "以下の行動データをもとに、客観的な記録文を作成してください。\n\n"
        "{{JSONデータ}}\n\n"
        "記述ルール：場所と時間を中心に述べ、主観的表現は使わない。\n"
        "expenses は場所推定に使えるものだけ自然に織り込み、説明調にはしない。\n"
        "30分未満の滞在は原則記載しない（移動として自然にまとめる場合のみ可）。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    LOGGER.info(
        "prompt_db: enabled=%s target=%s variant=%s time_style=%s",
        bool(cfg.prompt_db_id),
        cfg.prompt_target,
        cfg.prompt_variant,
        cfg.prompt_time_style or "(none)",
    )
    notion_system_prompt = fetch_prompt_from_notion(notion, cfg, "system") if cfg.prompt_db_id else None
    notion_user_prompt = fetch_prompt_from_notion(notion, cfg, "user") if cfg.prompt_db_id else None
    if notion_system_prompt:
        system_prompt = notion_system_prompt
    if notion_user_prompt:
        user_prompt = notion_user_prompt
        if "{payload_json}" in user_prompt:
            user_prompt = user_prompt.replace("{payload_json}", json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            user_prompt = f"{user_prompt}\n\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    LOGGER.info("prompt_source: type=system source=%s", "notion" if notion_system_prompt else "fallback")
    LOGGER.info("prompt_source: type=user source=%s", "notion" if notion_user_prompt else "fallback")

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI request failed ({resp.status_code}): {resp.text}")

    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    content = normalize_diary_output(content)
    if not content:
        raise RuntimeError("OpenAI response did not include diary text")
    return content


def normalize_diary_output(content: str) -> str:
    raw = (content or "").strip()
    if not raw:
        return ""

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    converted_lines: list[str] = []
    for line in lines:
        if line.startswith(("- ", "•", "・")):
            normalized = line[1:].strip() if line[0] in {"•", "・"} else line[2:].strip()
            if normalized:
                converted_lines.append(normalized)
            continue
        converted_lines.append(line)

    merged = " ".join(converted_lines)
    merged = re.sub(r"\s+", " ", merged).strip()
    merged = re.sub(r"([。！？!?])\s+", r"\1", merged)
    if not merged:
        return ""

    sentence_chunks = re.findall(r".*?[。！？!?]|.+$", merged)
    trimmed = "".join(sentence_chunks[:6]).strip()
    return trimmed


def generate_location_summary(
    window_start: datetime,
    window_end: datetime,
    tz_name: str,
    sessions: list[StaySession],
    tasks: list[TaskEvent],
    expenses: list[ExpenseEvent],
    openai_api_key: str,
    openai_model: str,
    notion: NotionClient,
    cfg: Config,
) -> str:
    main_sessions, short_sessions = partition_sessions(sessions, min_duration_min=30)
    movement_segments = classify_movement_segments(short_sessions, main_sessions)
    matched_tasks = match_tasks(tasks, main_sessions)
    payload = build_gpt_payload(
        window_start=window_start,
        window_end=window_end,
        tz_name=tz_name,
        main_sessions=main_sessions,
        movement_segments=movement_segments,
        matched_tasks=matched_tasks,
        expenses=expenses,
    )
    return generate_diary_from_gpt(payload, openai_api_key, openai_model, notion, cfg)


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

    LOGGER.info("window=[%s, %s)", window_start.isoformat(), window_end.isoformat())
    LOGGER.info("target diary_date=%s", diary_date)

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
                {"property": cfg.task_event_date_prop, "date": {"on_or_after": window_start.isoformat()}},
                {"property": cfg.task_event_date_prop, "date": {"before": window_end.isoformat()}},
            ]
        },
        "sorts": [{"property": cfg.task_event_date_prop, "direction": "ascending"}],
        "page_size": 100,
    }

    session_pages = notion.query_database(cfg.stay_sessions_db_id, sessions_query)
    task_pages = notion.query_database(cfg.task_db_id, task_query)
    expense_pages: list[dict[str, Any]] = []
    if cfg.expenses_db_id:
        expense_query = {
            "filter": {
                "or": [
                    {
                        "and": [
                            {"property": cfg.expense_received_at_prop, "date": {"on_or_after": window_start.isoformat()}},
                            {"property": cfg.expense_received_at_prop, "date": {"before": window_end.isoformat()}},
                        ]
                    },
                    {
                        "and": [
                            {"property": cfg.expense_received_at_prop, "date": {"is_empty": True}},
                            {"property": cfg.expense_date_prop, "date": {"on_or_after": window_start.date().isoformat()}},
                            {"property": cfg.expense_date_prop, "date": {"before": window_end.date().isoformat()}},
                        ]
                    },
                ]
            },
            "sorts": [{"property": cfg.expense_received_at_prop, "direction": "ascending"}],
            "page_size": 100,
        }
        expense_pages = notion.query_database(cfg.expenses_db_id, expense_query)

    sessions = merge_sessions(parse_stay_sessions(session_pages, window_start, window_end))
    tasks = parse_tasks_with_props(
        task_pages,
        window_start,
        window_end,
        event_date_prop=cfg.task_event_date_prop,
        status_prop=cfg.task_status_prop,
        title_prop=cfg.task_title_prop,
    )
    expenses = parse_expenses(expense_pages, cfg, window_start, window_end) if cfg.expenses_db_id else []
    main_sessions, _ = partition_sessions(sessions, min_duration_min=30)
    LOGGER.info("payload_counts: main_sessions=%s events=%s expenses=%s", len(main_sessions), len(tasks), len(expenses))
    summary_text = generate_location_summary(
        window_start,
        window_end,
        cfg.tz,
        sessions,
        tasks,
        expenses,
        cfg.openai_api_key,
        cfg.openai_model,
        notion,
        cfg,
    )
    LOGGER.info("fetched sessions=%s tasks=%s expenses=%s", len(sessions), len(tasks), len(expenses))

    page = find_daily_log_page(notion, cfg, diary_date)
    if not page:
        raise RuntimeError(f"Daily Log page not found for date={diary_date}")

    if cfg.dry_run:
        LOGGER.info("DRY_RUN=true: Notion page update skipped")
        return

    target_prop = (
        cfg.daily_log_gpt_location_summary_prop
        if os.getenv("DAILY_LOG_GPT_LOCATION_SUMMARY_PROP")
        else cfg.daily_log_location_summary_prop
    )
    patch = build_summary_property(page, target_prop, summary_text)
    notion.update_page_properties(page["id"], patch)
    LOGGER.info(
        "daily_log_updated: diary_date=%s page_id=%s property=%s chars=%s",
        diary_date,
        page["id"],
        target_prop,
        len(summary_text),
    )


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
