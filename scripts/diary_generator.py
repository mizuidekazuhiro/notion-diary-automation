from __future__ import annotations

import logging
import os
from typing import Any, Mapping

import requests

OPENAI_TIMEOUT = (5, 60)
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


def _build_prompts(input_fields: Mapping[str, str], target_date: str) -> tuple[str, str]:
    input_lines = "\n".join(f"- {name}: {value}" for name, value in input_fields.items())
    system_prompt = (
        "あなたはDaily Logを要約する記録アシスタントです。"
        "与えられた入力の事実だけを使い、第三者が観察・整理して記録したような客観文体で出力してください。"
    )
    user_prompt = (
        f"対象日: {target_date}\n"
        "以下のDaily Log関連プロパティを元に、100〜250字程度の日本語の日記文を作成してください。\n"
        "- 誇張しない\n"
        "- 入力にない事実を追加しない\n"
        "- 本人の独白・感想・反省にしない\n"
        "- 一人称を使わない\n"
        "- 本人の感情や内心を断定しない\n"
        "- 『〜してしまった』『〜と感じている』『〜と思った』『つい〜した』は使わない\n"
        "- 『〜していた』『〜した』『〜が見られた』『〜がうかがえる』『〜という様子だった』を優先\n"
        "- 箇条書き禁止\n"
        "- 簡潔で自然な文章\n"
        "- 情報が少ない場合も無理に膨らませない\n"
        "- 事実関係を維持する\n\n"
        f"入力プロパティ:\n{input_lines}"
    )
    return system_prompt, user_prompt


def generate_diary_from_daily_log(input_fields: Mapping[str, str], target_date: str) -> str:
    normalized_fields = {
        name: value.strip() for name, value in input_fields.items() if value and value.strip()
    }
    if not normalized_fields:
        raise ValueError("input_fields is empty")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
    if not api_key:
        logging.error(
            "OPENAI_API_KEY is missing; cannot generate diary. target_date=%s model=%s",
            target_date,
            model,
        )
        raise RuntimeError("OPENAI_API_KEY is missing")

    system_prompt, user_prompt = _build_prompts(normalized_fields, target_date)
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
