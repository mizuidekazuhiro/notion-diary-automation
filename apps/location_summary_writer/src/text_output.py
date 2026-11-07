"""Formatting and Notion text projection without acquisition or writes.

Normalize presentation only; preserve every generated event/date marker and
split the complete value into bounded items instead of truncating content.
"""
from __future__ import annotations

import re
from typing import Any


NOTION_TEXT_CHUNK_LENGTH = 1900


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

    return merged


def build_summary_property(page: dict[str, Any], property_name: str, text: str) -> dict[str, Any]:
    prop = page.get("properties", {}).get(property_name)
    if not prop:
        raise KeyError(f"Daily Log property not found: {property_name}")

    ptype = prop.get("type")
    if ptype in ("rich_text", "title"):
        chunks = [text[i:i + NOTION_TEXT_CHUNK_LENGTH] for i in range(0, len(text), NOTION_TEXT_CHUNK_LENGTH)] or [""]
        return {property_name: {ptype: [{"type": "text", "text": {"content": chunk}} for chunk in chunks]}}

    raise TypeError(f"Property {property_name} type is unsupported for text update: {ptype}")
