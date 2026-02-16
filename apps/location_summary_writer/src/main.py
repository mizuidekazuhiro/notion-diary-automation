import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

NOTION_VERSION = "2022-06-28"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


@dataclass
class Config:
    notion_token: str
    location_log_db_id: str
    daily_log_db_id: str
    openai_api_key: str

    tz: str = "Asia/Tokyo"
    window_start_hour: int = 5

    daily_log_date_prop: str = "Date"
    daily_log_location_summary_prop: str = "Location summary"

    location_log_time_prop: str = "Time"
    location_log_place_prop: str = "Place"
    location_log_lat_prop: str = "Latitude (raw)"
    location_log_lon_prop: str = "Longitude (raw)"
    location_log_source_prop: str = "Source"

    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    dry_run: bool = False

    location_round_decimals: int = 4
    time_bucket_minutes: int = 5
    openai_max_retries: int = 4


@dataclass
class LocationLog:
    page_id: str
    timestamp: datetime
    place: str
    lat: float | None
    lon: float | None
    source: str


@dataclass
class Segment:
    start: datetime
    end: datetime
    place_label: str
    rounded_lat: float | None
    rounded_lon: float | None
    raw_count: int


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

    def get_page(self, page_id: str) -> dict[str, Any]:
        url = f"https://api.notion.com/v1/pages/{page_id}"
        resp = self.session.get(url, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion get page failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def update_page_properties(self, page_id: str, properties: dict[str, Any]) -> None:
        url = f"https://api.notion.com/v1/pages/{page_id}"
        resp = self.session.patch(url, json={"properties": properties}, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"Notion update failed ({resp.status_code}): {resp.text}")


class OpenAIClient:
    def __init__(self, api_key: str, base_url: str, max_retries: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )

    def generate_summary(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        system_prompt = "位置ログから日記風の行動記録を作る。推測禁止。出力はJSONのみ。"
        user_prompt = (
            "以下の入力データをもとに、指定のJSONスキーマで必ず返答してください。"
            "店名/施設名/目的/同行者/活動内容の推測は禁止。"
            "\n\n"
            f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "JSON_SCHEMA:\n"
            "{\n"
            '  "location_summary_text": "...",\n'
            '  "primary_place_label": "...",\n'
            '  "stats": {\n'
            '    "window_start": "...",\n'
            '    "window_end": "...",\n'
            '    "move_count": 0,\n'
            '    "first_seen": "HH:MM",\n'
            '    "last_seen": "HH:MM",\n'
            '    "top_places": [{ "place_label": "...", "duration_min": 0, "visits": 0 }],\n'
            '    "data_quality_notes": []\n'
            "  }\n"
            "}\n"
        )

        req_body = {
            "model": model,
            "temperature": 0.2,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "location_summary",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "location_summary_text": {"type": "string"},
                            "primary_place_label": {"type": "string"},
                            "stats": {
                                "type": "object",
                                "properties": {
                                    "window_start": {"type": "string"},
                                    "window_end": {"type": "string"},
                                    "move_count": {"type": "integer"},
                                    "first_seen": {"type": "string"},
                                    "last_seen": {"type": "string"},
                                    "top_places": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "place_label": {"type": "string"},
                                                "duration_min": {"type": "integer"},
                                                "visits": {"type": "integer"},
                                            },
                                            "required": ["place_label", "duration_min", "visits"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "data_quality_notes": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": [
                                    "window_start",
                                    "window_end",
                                    "move_count",
                                    "first_seen",
                                    "last_seen",
                                    "top_places",
                                    "data_quality_notes",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["location_summary_text", "primary_place_label", "stats"],
                        "additionalProperties": False,
                    },
                },
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        for attempt in range(1, self.max_retries + 1):
            resp = self.session.post(url, json=req_body, timeout=60)
            retriable = resp.status_code in {429, 500, 502, 503, 504}
            if resp.status_code < 400:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)

            if retriable and attempt < self.max_retries:
                sleep_s = 2 ** (attempt - 1)
                print(f"OpenAI retriable error {resp.status_code}, retry after {sleep_s}s")
                time.sleep(sleep_s)
                continue

            raise RuntimeError(f"OpenAI call failed ({resp.status_code}): {resp.text}")

        raise RuntimeError("OpenAI call exhausted retries")


def load_config() -> Config:
    required = ["NOTION_TOKEN", "LOCATION_LOG_DB_ID", "DAILY_LOG_DB_ID", "OPENAI_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")

    return Config(
        notion_token=os.environ["NOTION_TOKEN"],
        location_log_db_id=os.environ["LOCATION_LOG_DB_ID"],
        daily_log_db_id=os.environ["DAILY_LOG_DB_ID"],
        openai_api_key=os.environ["OPENAI_API_KEY"],
        tz=os.getenv("TZ", "Asia/Tokyo"),
        window_start_hour=int(os.getenv("WINDOW_START_HOUR", "5")),
        daily_log_date_prop=os.getenv("DAILY_LOG_DATE_PROP", "Date"),
        daily_log_location_summary_prop=os.getenv("DAILY_LOG_LOCATION_SUMMARY_PROP", "Location summary"),
        location_log_time_prop=os.getenv("LOCATION_LOG_TIME_PROP", "Time"),
        location_log_place_prop=os.getenv("LOCATION_LOG_PLACE_PROP", "Place"),
        location_log_lat_prop=os.getenv("LOCATION_LOG_LAT_PROP", "Latitude (raw)"),
        location_log_lon_prop=os.getenv("LOCATION_LOG_LON_PROP", "Longitude (raw)"),
        location_log_source_prop=os.getenv("LOCATION_LOG_SOURCE_PROP", "Source"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        openai_base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
        location_round_decimals=int(os.getenv("LOCATION_ROUND_DECIMALS", "4")),
        time_bucket_minutes=int(os.getenv("TIME_BUCKET_MINUTES", "5")),
        openai_max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "4")),
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
    ptype = prop.get("type")
    if ptype == "number":
        return prop.get("number")
    text = rich_text_plain(prop)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_location_logs(pages: list[dict[str, Any]], cfg: Config) -> list[LocationLog]:
    logs: list[LocationLog] = []
    for page in pages:
        props = page.get("properties", {})
        time_prop = props.get(cfg.location_log_time_prop, {})
        date_obj = time_prop.get("date") if time_prop.get("type") == "date" else None
        if not date_obj or not date_obj.get("start"):
            continue

        logs.append(
            LocationLog(
                page_id=page["id"],
                timestamp=parse_datetime(date_obj["start"]),
                place=rich_text_plain(props.get(cfg.location_log_place_prop)).strip() or "不明な場所",
                lat=parse_number(props.get(cfg.location_log_lat_prop)),
                lon=parse_number(props.get(cfg.location_log_lon_prop)),
                source=rich_text_plain(props.get(cfg.location_log_source_prop)).strip(),
            )
        )

    logs.sort(key=lambda x: x.timestamp)
    return logs


def segment_logs(logs: list[LocationLog], cfg: Config, window_end: datetime) -> list[Segment]:
    if not logs:
        return []

    segments: list[Segment] = []

    def rounded(v: float | None) -> float | None:
        if v is None:
            return None
        return round(v, cfg.location_round_decimals)

    current_key = None
    current_start = logs[0].timestamp
    current_place = logs[0].place
    current_lat = rounded(logs[0].lat)
    current_lon = rounded(logs[0].lon)
    current_count = 0

    for idx, log in enumerate(logs):
        key = (rounded(log.lat), rounded(log.lon))
        if current_key is None:
            current_key = key

        if key != current_key:
            segments.append(
                Segment(
                    start=current_start,
                    end=log.timestamp,
                    place_label=current_place,
                    rounded_lat=current_lat,
                    rounded_lon=current_lon,
                    raw_count=current_count,
                )
            )
            current_key = key
            current_start = log.timestamp
            current_place = log.place
            current_lat = rounded(log.lat)
            current_lon = rounded(log.lon)
            current_count = 0

        current_count += 1

        if idx == len(logs) - 1:
            segments.append(
                Segment(
                    start=current_start,
                    end=window_end,
                    place_label=current_place,
                    rounded_lat=current_lat,
                    rounded_lon=current_lon,
                    raw_count=current_count,
                )
            )

    return segments


def build_openai_payload(window_start: datetime, window_end: datetime, diary_date: str, segments: list[Segment]) -> dict[str, Any]:
    return {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "diary_date": diary_date,
        "segments": [
            {
                "start": s.start.isoformat(),
                "end": s.end.isoformat(),
                "place_label": s.place_label,
                "rounded_lat": s.rounded_lat,
                "rounded_lon": s.rounded_lon,
                "raw_count": s.raw_count,
                "duration_min": max(0, int((s.end - s.start).total_seconds() // 60)),
            }
            for s in segments
        ],
    }


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


def fallback_summary(window_start: datetime, window_end: datetime, segments: list[Segment]) -> dict[str, Any]:
    header = f"対象: {window_start.strftime('%m/%d %H:%M')}〜{window_end.strftime('%m/%d %H:%M')}"
    if not segments:
        text = f"{header}\n位置ログがありませんでした"
        return {
            "location_summary_text": text,
            "primary_place_label": "",
            "stats": {
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "move_count": 0,
                "first_seen": "",
                "last_seen": "",
                "top_places": [],
                "data_quality_notes": ["location logs not found"],
            },
        }

    timeline = []
    for seg in segments[:6]:
        timeline.append(f"- {seg.start.strftime('%H:%M')}〜{seg.end.strftime('%H:%M')} {seg.place_label}")
    body = "\n".join(
        [
            header,
            "位置データを要約しました。",
            "主に滞在したエリアと移動時刻を記録します。",
            *timeline,
        ]
    )
    return {
        "location_summary_text": body,
        "primary_place_label": segments[0].place_label,
        "stats": {
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "move_count": max(0, len(segments) - 1),
            "first_seen": segments[0].start.strftime("%H:%M"),
            "last_seen": segments[-1].end.strftime("%H:%M"),
            "top_places": [],
            "data_quality_notes": ["fallback summary used"],
        },
    }


def run() -> None:
    cfg = load_config()
    now = datetime.now(timezone.utc)
    window_start, window_end, diary_date = compute_window(now, cfg.tz, cfg.window_start_hour)

    print(f"Window: [{window_start.isoformat()}, {window_end.isoformat()})")
    print(f"Target diary_date: {diary_date}")

    notion = NotionClient(cfg.notion_token)

    location_query = {
        "filter": {
            "and": [
                {"property": cfg.location_log_time_prop, "date": {"on_or_after": window_start.isoformat()}},
                {"property": cfg.location_log_time_prop, "date": {"before": window_end.isoformat()}},
            ]
        },
        "sorts": [{"property": cfg.location_log_time_prop, "direction": "ascending"}],
        "page_size": 100,
    }
    pages = notion.query_database(cfg.location_log_db_id, location_query)
    logs = parse_location_logs(pages, cfg)
    segments = segment_logs(logs, cfg, window_end)
    print(f"Fetched logs={len(logs)} segments={len(segments)}")

    if logs:
        openai_payload = build_openai_payload(window_start, window_end, diary_date, segments)
        openai_client = OpenAIClient(cfg.openai_api_key, cfg.openai_base_url, cfg.openai_max_retries)
        summary_json = openai_client.generate_summary(cfg.openai_model, openai_payload)
    else:
        summary_json = fallback_summary(window_start, window_end, [])

    summary_text = summary_json.get("location_summary_text", "").strip()
    if not summary_text:
        raise ValueError("Empty location_summary_text from summary generation")

    page = find_daily_log_page(notion, cfg, diary_date)
    if not page:
        raise RuntimeError(f"Daily Log page not found for date={diary_date}")

    if cfg.dry_run:
        print("DRY_RUN=true: Notion page update skipped")
        print("Generated summary preview:")
        print(summary_text)
        return

    full_page = notion.get_page(page["id"])
    patch = build_summary_property(full_page, cfg.daily_log_location_summary_prop, summary_text)
    notion.update_page_properties(page["id"], patch)
    print("Daily Log Location summary updated successfully")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        sys.exit(1)
