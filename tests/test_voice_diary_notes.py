from __future__ import annotations

import types

from scripts import voice_diary_notes
from scripts.voice_diary_notes import VoiceDiaryNote, fetch_voice_diary_notes, format_voice_diary_notes


def test_format_voice_diary_notes_orders_lines() -> None:
    notes = [
        VoiceDiaryNote(page_id="1", recorded_at="2026-05-01T00:10:00+00:00", text="朝メモ", source="ios", note_hash="a"),
        VoiceDiaryNote(page_id="2", recorded_at="2026-05-01T04:20:00+00:00", text="昼メモ", source="ios", note_hash="b"),
    ]
    rendered = format_voice_diary_notes(notes)
    assert "[00:10] 朝メモ" in rendered
    assert "[04:20] 昼メモ" in rendered


def test_fetch_returns_empty_when_db_id_missing(monkeypatch) -> None:
    monkeypatch.delenv("VOICE_DIARY_NOTES_DB_ID", raising=False)
    assert fetch_voice_diary_notes("2026-05-01") == []


def test_fetch_returns_empty_on_notion_error(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_DIARY_NOTES_DB_ID", "db")
    monkeypatch.setenv("NOTION_API_KEY", "token")

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(voice_diary_notes.requests, "post", _boom)
    assert fetch_voice_diary_notes("2026-05-01") == []


def test_fetch_uses_notion_token_fallback(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_DIARY_NOTES_DB_ID", "db")
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.setenv("NOTION_TOKEN", "fallback-token")

    class DummyResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "id": "page1",
                        "properties": {
                            "Recorded At": {"date": {"start": "2026-05-01T00:10:00+00:00"}},
                            "Text": {"rich_text": [{"plain_text": "メモ"}]},
                            "Source": {"select": {"name": "ios"}},
                            "Note Hash": {"rich_text": [{"plain_text": "h1"}]},
                            "Status": {"select": {"name": "new"}},
                        },
                    }
                ]
            }

    monkeypatch.setattr(voice_diary_notes.requests, "post", lambda *args, **kwargs: DummyResp())
    notes = fetch_voice_diary_notes("2026-05-01")
    assert len(notes) == 1
    assert notes[0].text == "メモ"


def test_diary_prompt_mentions_voice_notes_policy() -> None:
    from scripts.diary_generator import _build_prompts

    system_prompt, _ = _build_prompts({"Voice Diary Notes": "[09:10] test"}, "2026-05-01")
    assert "Voice Diary Notes は本人がその時点で残した一次メモ" in system_prompt
    assert "単純に全列挙しない" in system_prompt
