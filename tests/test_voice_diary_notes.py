from __future__ import annotations

from scripts.voice_diary_notes import VoiceDiaryNote, format_voice_diary_notes


def test_format_voice_diary_notes_orders_lines() -> None:
    notes = [
        VoiceDiaryNote(page_id="1", recorded_at="2026-05-01T00:10:00+00:00", text="朝メモ", source="ios", note_hash="a"),
        VoiceDiaryNote(page_id="2", recorded_at="2026-05-01T04:20:00+00:00", text="昼メモ", source="ios", note_hash="b"),
    ]
    rendered = format_voice_diary_notes(notes)
    assert "[00:10] 朝メモ" in rendered
    assert "[04:20] 昼メモ" in rendered
