"""Role-specific model configuration; shared OPENAI_MODEL cannot override it."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROLE_DEFAULTS = {
    "notes": ("NOTES_LABEL_MODEL", "gpt-4.1-mini"),
    "location": ("LOCATION_SUMMARY_MODEL", "gpt-4o-mini"),
    "diary": ("DIARY_MODEL", "gpt-4.1-mini"),
    "advice": ("TODAY_ADVICE_FINAL_MODEL", "gpt-4.1"),
    "consistency": ("DIARY_CONSISTENCY_MODEL", "gpt-4.1"),
}


def model_for(role: str) -> str:
    variable, default = ROLE_DEFAULTS[role]
    return os.getenv(variable, "").strip() or default


def request_settings(model: str) -> dict:
    if model.startswith("gpt-6"):
        return {"max_completion_tokens": 4096,
                "reasoning_effort": "low" if model == "gpt-6.1-sol" else "none"}
    return {"max_tokens": 4096, "temperature": 0.2}


def generation_signature(role: str, filenames: tuple[str, ...]) -> dict:
    root = Path(__file__).resolve().parents[1]
    model = model_for(role)
    return {"model": model, "request_settings": request_settings(model),
            "assets": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in filenames}}
