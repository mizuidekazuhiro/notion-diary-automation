"""Role-specific model configuration; shared OPENAI_MODEL cannot override it."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal, TypedDict

ModelRole = Literal["notes", "location", "diary", "advice", "sleep", "consistency"]
MAX_OUTPUT_TOKENS = 4096
LEGACY_TEMPERATURE = 0.2


class RequestSettings(TypedDict, total=False):
    max_completion_tokens: int
    reasoning_effort: Literal["none", "low"]
    max_tokens: int
    temperature: float


class GenerationSignature(TypedDict):
    model: str
    request_settings: RequestSettings
    assets: dict[str, str]


# Precedence: nonempty role-specific environment value, then this registry.
# Generic OPENAI_MODEL is deliberately excluded from these roles.
ROLE_DEFAULTS: dict[ModelRole, tuple[str, str]] = {
    "notes": ("NOTES_LABEL_MODEL", "gpt-6-luna"),
    "location": ("LOCATION_SUMMARY_MODEL", "gpt-6.1-terra"),
    "diary": ("DIARY_MODEL", "gpt-6.1-terra"),
    "advice": ("TODAY_ADVICE_FINAL_MODEL", "gpt-6.1-sol"),
    "sleep": ("SLEEP_INSIGHT_MODEL", "gpt-6.1-sol"),
    "consistency": ("DIARY_CONSISTENCY_MODEL", "gpt-6.1-sol"),
}


def model_for(role: ModelRole) -> str:
    variable, default = ROLE_DEFAULTS[role]
    return os.getenv(variable, "").strip() or default


def request_settings(model: str) -> RequestSettings:
    if model.startswith("gpt-6"):
        return {"max_completion_tokens": MAX_OUTPUT_TOKENS,
                "reasoning_effort": "low" if model == "gpt-6.1-sol" else "none"}
    return {"max_tokens": MAX_OUTPUT_TOKENS, "temperature": LEGACY_TEMPERATURE}


def generation_signature(role: ModelRole, filenames: tuple[str, ...]) -> GenerationSignature:
    root = Path(__file__).resolve().parents[1]
    model = model_for(role)
    return {"model": model, "request_settings": request_settings(model),
            "assets": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in filenames}}
