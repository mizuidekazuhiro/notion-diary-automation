from __future__ import annotations

import os
from typing import Any

import requests

OPENAI_TIMEOUT = 30


def chat_completion(*, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "temperature": temperature,
            "messages": _build_chat_messages(system_prompt=system_prompt, user_prompt=user_prompt),
        },
        timeout=OPENAI_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    content: Any = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI response did not include content")
    return content.strip()


def _build_chat_messages(*, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
