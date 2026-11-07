"""Persisted generation-state access and exact readback checks, without I/O."""
from __future__ import annotations

import json
from typing import Any, Literal, Mapping, TypedDict

GenerationStatus = Literal["success", "skipped", "insufficient_data", "degraded", "failed", "fallback"]


class GenerationDetails(TypedDict, total=False):
    status: GenerationStatus
    method: str
    fallback_used: bool
    rejection_reason: str | None
    model: str
    input_hash: str
    target_date: str
    reflection_date: str


def read_generation_metadata(summary: object) -> dict[str, Any]:
    try:
        value = json.loads(getattr(summary, "generation_metadata", None) or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def merge_generation_metadata(summary: object, stage: str, metadata: Mapping[str, Any]) -> str:
    previous = read_generation_metadata(summary)
    previous[stage] = {key: value for key, value in metadata.items() if key != "text"}
    return json.dumps(previous, ensure_ascii=False, separators=(",", ":"))


def stage_generation_status(summary: object, stage: str) -> str:
    stage_value = read_generation_metadata(summary).get(stage)
    if not isinstance(stage_value, dict):
        return ""
    return str(stage_value.get("status") or "")


def assert_generation_readback(summary: object, expected: Mapping[str, Any]) -> None:
    mismatches = []
    for key, value in expected.items():
        if key.endswith("_generated_at"):
            continue
        actual = getattr(summary, key, None)
        if key in {"generation_metadata", "diary_evidence_json"}:
            try:
                equal = json.loads(actual or "null") == json.loads(value)
            except (ValueError, TypeError):
                equal = False
        else:
            equal = str(actual or "").replace("\r\n", "\n").strip() == str(value or "").replace("\r\n", "\n").strip()
        if not equal:
            mismatches.append(key)
    if mismatches:
        raise RuntimeError("generation_readback_mismatch fields=" + ",".join(mismatches))
