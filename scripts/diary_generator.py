from __future__ import annotations

import logging
import os
from typing import Any, Mapping

import requests

OPENAI_TIMEOUT = (5, 60)
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


def _build_prompts(input_fields: Mapping[str, str], target_date: str) -> tuple[str, str]:
    ordered_fields = [
        ("Date", input_fields.get("Date", "")),
        ("Target Date", input_fields.get("Target Date", "")),
        ("Mood", input_fields.get("Mood", "")),
        ("Notes", input_fields.get("Notes", "")),
        ("Place", input_fields.get("Place", "")),
        ("Done Count", input_fields.get("Done Count", "")),
        ("Done Tasks", input_fields.get("Done Tasks", "")),
        ("Drop Count", input_fields.get("Drop Count", "")),
        ("Drop Tasks", input_fields.get("Drop Tasks", "")),
        ("Expenses Total", input_fields.get("Expenses Total", "")),
        ("Expenses", input_fields.get("Expenses", "")),
        ("Location Summary", input_fields.get("Location summary", "")),
        ("Activity Summary", input_fields.get("Activity Summary", "")),
        ("Meal Summary", input_fields.get("Meal summary", "")),
        ("Kcal", input_fields.get("Kcal", "")),
        ("Protein", input_fields.get("Protein", "")),
        ("Fat", input_fields.get("Fat", "")),
        ("Carb", input_fields.get("Carb", "")),
        ("Weight", input_fields.get("Weight", "")),
    ]
    input_lines = "\n".join(f"{name}: {value}" for name, value in ordered_fields)

    system_prompt = (
        "あなたは Daily Log の複数プロパティをもとに、その日の内容を自然な日本語の日記文として要約するアシスタントです。\n\n"
        "重要:\n"
        "- Notes だけを見て日記を書いてはいけません\n"
        "- Daily Log 内の複数項目を必ず統合して、その日の全体像を書いてください\n"
        "- 入力にない情報を推測で補ってはいけません\n"
        "- 未入力の項目は無視してよいですが、存在する他項目は積極的に使ってください\n"
        "- 単なる項目の羅列ではなく、自然な日記文にしてください\n"
        "- Notes がある場合は本人の所感として活かしてよいですが、Notes の言い換えだけで終わってはいけません\n"
        "- 支出情報は `Expenses Total` と `Expenses` の両方を参照してください\n\n"
        "支出情報の扱い:\n"
        "- `Expenses Total` はその日の支出総額として扱います\n"
        "- `Expenses` はリレーション先の支出明細として扱います\n"
        "- 明細がある場合は、特徴的な支出先や支出傾向を自然に反映してよいです\n"
        "- ただし、支出理由や気持ちは推測してはいけません\n\n"
        "参照優先度:\n"
        "1. Location summary / Activity Summary\n"
        "2. Done Tasks / Drop Tasks / Done Count / Drop Count\n"
        "3. Notes\n"
        "4. Expenses Total / Expenses\n"
        "5. Meal summary / Kcal / Protein / Fat / Carb / Weight\n"
        "6. Mood\n\n"
        "出力ルール:\n"
        "- 日本語で出力する\n"
        "- 事実ベースで書く\n"
        "- 行動、進捗、支出、生活面の振り返りを自然に統合する\n"
        "- 短すぎる要約ではなく、やや詳しめの日記文にする\n"
        "- 目安は5〜10文程度、情報量が多い日は10〜12文程度でもよい\n"
        "- 情報が少ない日は無理に長くしなくてよい\n"
        "- 前半でその日の流れ、後半で振り返りを書く構成にしてよい"
    )
    user_prompt = (
        "以下はある1日の Daily Log です。\n"
        "Notes だけではなく、全ての入力項目を確認して、その日の全体像が分かる、"
        "やや詳しめの自然な日記文を作成してください。\n"
        "短すぎる要約ではなく、流れと振り返りが分かる文章にしてください。\n"
        "入力にない事実は書かないでください。\n\n"
        f"Date: {input_fields.get('Date', target_date)}\n"
        f"Target Date: {input_fields.get('Target Date', target_date)}\n"
        f"Mood: {input_fields.get('Mood', '')}\n"
        f"Notes: {input_fields.get('Notes', '')}\n"
        f"Place: {input_fields.get('Place', '')}\n"
        f"Done Count: {input_fields.get('Done Count', '')}\n"
        f"Done Tasks: {input_fields.get('Done Tasks', '')}\n"
        f"Drop Count: {input_fields.get('Drop Count', '')}\n"
        f"Drop Tasks: {input_fields.get('Drop Tasks', '')}\n"
        f"Expenses Total: {input_fields.get('Expenses Total', '')}\n"
        f"Expenses: {input_fields.get('Expenses', '')}\n"
        f"Location Summary: {input_fields.get('Location summary', '')}\n"
        f"Activity Summary: {input_fields.get('Activity Summary', '')}\n"
        f"Meal Summary: {input_fields.get('Meal summary', '')}\n"
        f"Kcal: {input_fields.get('Kcal', '')}\n"
        f"Protein: {input_fields.get('Protein', '')}\n"
        f"Fat: {input_fields.get('Fat', '')}\n"
        f"Carb: {input_fields.get('Carb', '')}\n"
        f"Weight: {input_fields.get('Weight', '')}\n\n"
        f"入力プロパティ(参考):\n{input_lines}"
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
