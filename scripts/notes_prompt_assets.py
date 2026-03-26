from __future__ import annotations

from pathlib import Path
import json
from typing import Any

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "notes_structured_prompt.json"


def load_notes_prompt_assets() -> dict[str, Any]:
    return json.loads(PROMPT_FILE.read_text(encoding="utf-8"))
