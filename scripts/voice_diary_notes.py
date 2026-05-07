from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

import requests

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_MAX_COUNT = 50
DEFAULT_MAX_CHARS = 6000


@dataclass(frozen=True)
class VoiceDiaryNote:
    page_id: str
    recorded_at: str
    text: str
    source: str
    note_hash: str


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _read_rich_text(prop: dict[str, Any]) -> str:
    texts = prop.get("rich_text") or []
    out = []
    for row in texts:
        plain = str(row.get("plain_text") or "").strip()
        if plain:
            out.append(plain)
    return "".join(out)


def _read_select_name(prop: dict[str, Any]) -> str:
    select = prop.get("select")
    if isinstance(select, dict):
        return str(select.get("name") or "").strip()
    return ""


def _parse_note(page: dict[str, Any]) -> Optional[VoiceDiaryNote]:
    props = page.get("properties") or {}
    page_id = str(page.get("id") or "").strip()
    recorded_at = str((props.get("Recorded At") or {}).get("date", {}).get("start") or "").strip()
    text = _read_rich_text(props.get("Text") or {})
    source = _read_select_name(props.get("Source") or {})
    note_hash = _read_rich_text(props.get("Note Hash") or {})
    status_name = _read_select_name(props.get("Status") or {}).lower()
    if not page_id or not recorded_at or not text or status_name == "ignored":
        return None
    return VoiceDiaryNote(page_id=page_id, recorded_at=recorded_at, text=text, source=source, note_hash=note_hash)


def _resolve_notion_token() -> str:
    return (os.getenv("NOTION_API_KEY", "") or "").strip() or (os.getenv("NOTION_TOKEN", "") or "").strip()


def fetch_voice_diary_notes(target_date: str) -> list[VoiceDiaryNote]:
    db_id = (os.getenv("VOICE_DIARY_NOTES_DB_ID", "") or "").strip()
    if not db_id:
        logging.info("voice_diary_notes_fetch_skipped_env_missing target_date=%s", target_date)
        return []
    notion_token = _resolve_notion_token()
    if not notion_token:
        logging.warning("voice_diary_notes_fetch_skipped_env_missing target_date=%s reason=notion_token_missing", target_date)
        return []

    max_count = _env_int("VOICE_DIARY_NOTES_MAX_COUNT", DEFAULT_MAX_COUNT)
    max_chars = _env_int("VOICE_DIARY_NOTES_MAX_CHARS", DEFAULT_MAX_CHARS)
    logging.info("voice_diary_notes_fetch_start target_date=%s max_count=%s max_chars=%s", target_date, max_count, max_chars)

    payload = {
        "filter": {
            "and": [
                {"property": "Target Date", "date": {"equals": target_date}},
                {"property": "Status", "select": {"does_not_equal": "ignored"}},
                {"property": "Text", "rich_text": {"is_not_empty": True}},
            ]
        },
        "sorts": [{"property": "Recorded At", "direction": "ascending"}],
        "page_size": min(100, max_count * 2),
    }
    try:
        resp = requests.post(f"{NOTION_API_BASE}/databases/{db_id}/query", headers=_notion_headers(notion_token), json=payload, timeout=(5, 60))
        resp.raise_for_status()
        response_json = resp.json()
        rows = response_json.get("results")
        if not isinstance(rows, list):
            raise ValueError("results is not list")
    except Exception as exc:
        logging.warning("voice_diary_notes_fetch_failed target_date=%s error=%s", target_date, exc)
        return []

    parsed: list[VoiceDiaryNote] = []
    for row in rows:
        note = _parse_note(row)
        if note is not None:
            parsed.append(note)

    deduped: list[VoiceDiaryNote] = []
    seen_hashes: set[str] = set()
    for note in parsed:
        key = (note.note_hash or "").strip()
        if key and key in seen_hashes:
            continue
        if key:
            seen_hashes.add(key)
        deduped.append(note)
    logging.info("voice_diary_notes_deduped target_date=%s before=%s after=%s", target_date, len(parsed), len(deduped))

    limited: list[VoiceDiaryNote] = []
    total_chars = 0
    for note in deduped:
        line = format_voice_diary_notes([note])
        next_chars = total_chars + len(line)
        if len(limited) >= max_count or next_chars > max_chars:
            logging.info("voice_diary_notes_truncated target_date=%s kept=%s total=%s max_count=%s max_chars=%s", target_date, len(limited), len(deduped), max_count, max_chars)
            break
        limited.append(note)
        total_chars = next_chars

    logging.info("voice_diary_notes_fetch_done target_date=%s count=%s", target_date, len(limited))
    return limited


def format_voice_diary_notes(notes: Iterable[VoiceDiaryNote]) -> str:
    lines: list[str] = []
    for note in notes:
        time_text = ""
        try:
            iso_text = note.recorded_at.replace("Z", "+00:00")
            time_text = datetime.fromisoformat(iso_text).strftime("%H:%M")
        except ValueError:
            time_text = note.recorded_at
        lines.append(f"[{time_text}] {note.text.strip()}")
    return "\n".join(lines)


def mark_voice_diary_notes_used(notes: Iterable[VoiceDiaryNote], *, daily_log_page_id: str) -> None:
    notion_token = _resolve_notion_token()
    if not notion_token:
        return
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    for note in notes:
        try:
            payload = {
                "properties": {
                    "Status": {"select": {"name": "used"}},
                    "Used At": {"date": {"start": now}},
                    "Daily Log Page ID": {"rich_text": [{"type": "text", "text": {"content": daily_log_page_id}}]},
                }
            }
            resp = requests.patch(f"{NOTION_API_BASE}/pages/{note.page_id}", headers=_notion_headers(notion_token), json=payload, timeout=(5, 60))
            resp.raise_for_status()
        except Exception as exc:
            logging.warning("voice_diary_notes_mark_used_failed page_id=%s error=%s", note.page_id, exc)
            continue
    logging.info("voice_diary_notes_mark_used_done daily_log_page_id=%s", daily_log_page_id)
