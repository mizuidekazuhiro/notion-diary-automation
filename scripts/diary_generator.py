from __future__ import annotations

import logging
import os
from typing import Any

import requests

OPENAI_TIMEOUT = (5, 60)
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


def _build_prompts(notes_text: str, target_date: str) -> tuple[str, str]:
    system_prompt = (
        "あなたは誠実な日記作成アシスタントです。"
        "与えられたNotesの事実のみを使って、自然な日本語の日記文を作成してください。"
    )
    user_prompt = (
        f"対象日: {target_date}\n"
        "以下のNotesを元に、100〜250字程度の自然な日記文を日本語で作成してください。\n"
        "- 誇張しない\n"
        "- Notesにない事実を追加しない\n"
        "- 一人称で自然な日記調\n"
        "- 箇条書き禁止\n"
        "- 簡潔で自然な文章\n"
        "- Notesが短い場合も無理に膨らませない\n"
        "- 事実関係を維持する\n\n"
        f"Notes:\n{notes_text.strip()}"
    )
    return system_prompt, user_prompt


def generate_diary_from_notes(notes_text: str, target_date: str) -> str:
    notes = (notes_text or "").strip()
    if not notes:
        raise ValueError("notes_text is empty")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
    if not api_key:
        logging.error(
            "OPENAI_API_KEY is missing; cannot generate diary. target_date=%s model=%s",
            target_date,
            model,
        )
        raise RuntimeError("OPENAI_API_KEY is missing")

    system_prompt, user_prompt = _build_prompts(notes, target_date)
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    response: requests.Response | None = None
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json=payload,
            timeout=OPENAI_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        diary = content.strip() if isinstance(content, str) else ""
        if not diary:
            logging.error(
                "OpenAI diary generation returned empty content. target_date=%s model=%s status_code=%s response_text=%s",
                target_date,
                model,
                response.status_code,
                response.text[:1000],
            )
            raise RuntimeError("OpenAI returned empty diary")
        return diary
    except requests.RequestException as exc:
        status_code = response.status_code if response is not None else None
        response_text = response.text[:1000] if response is not None else ""
        logging.error(
            "OpenAI diary generation failed. exception_class=%s exception_message=%s status_code=%s response_text=%s model=%s",
            exc.__class__.__name__,
            str(exc),
            status_code,
            response_text,
            model,
        )
        raise
    except Exception as exc:
        status_code = response.status_code if response is not None else None
        response_text = response.text[:1000] if response is not None else ""
        logging.error(
            "OpenAI diary generation failed. exception_class=%s exception_message=%s status_code=%s response_text=%s model=%s",
            exc.__class__.__name__,
            str(exc),
            status_code,
            response_text,
            model,
        )
        raise
