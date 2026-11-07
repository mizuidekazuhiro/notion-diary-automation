"""Bounded read-only evaluation. No Notion mutation or mail credentials are used."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
from publish.read_daily_log import parse_daily_log_payload
from scripts.daily_job import build_diary_input_fields
from scripts.diary_generator import _build_prompts, SENTENCE_FORMAT, diary_sources, validate_sentences
from scripts.openai_chat_utils import chat_completion
from apps.location_summary_writer.src import main as location_writer
from scripts.notes_prompt_assets import load_notes_prompt_assets

# Standard USD / million tokens, checked against official model pages.
PRICES = {"gpt-4.1-mini": (.4, .1, 1.6), "gpt-4o-mini": (.15, .075, .6),
          "gpt-6-luna": (.1, .01, .5), "gpt-6.1-terra": (1, .05, 5), "gpt-6.1-sol": (2, .1, 10)}


def notion_read(path: str, body: dict | None = None) -> dict:
    if not (path.startswith("/pages/") or path.startswith("/databases/")) or (body is not None and not path.endswith("/query")):
        raise ValueError("read_only_source_client")
    headers = {"Authorization": "Bearer " + os.environ["NOTION_TOKEN"], "Notion-Version": "2022-06-28"}
    for attempt in range(5):
        response = requests.request("POST" if body is not None else "GET", "https://api.notion.com/v1" + path,
                                    headers=headers, json=body, timeout=(5, 45))
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 4:
                time.sleep(float(response.headers.get("Retry-After", min(16, 2 ** attempt))))
                continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("source_read_failed")


def property_value(prop: dict):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in {"title", "rich_text"}:
        return "".join(item.get("plain_text", item.get("text", {}).get("content", "")) for item in value or [])
    if kind in {"select", "status"}:
        return (value or {}).get("name")
    if kind == "date":
        return (value or {}).get("start")
    if kind == "relation":
        return [item["id"] for item in value or []]
    if kind == "rollup":
        return (value or {}).get("number")
    return value if kind in {"number", "checkbox"} else None


def case_fields(page: dict, task_cache: dict, *, include_summary: bool = False):
    props = {key: property_value(value) for key, value in page.get("properties", {}).items()}
    payload = {re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_"): value for key, value in props.items()}
    target = str(props.get("Target Date") or props.get("Date") or "")[:10]
    payload.update(found=True, target_date=target, page_id=page["id"], title=props.get("名前", ""))
    for relation, destination in (("Done Tasks", "done_tasks"), ("Drop Tasks", "drop_tasks")):
        tasks = []
        for page_id in props.get(relation) or []:
            if page_id not in task_cache:
                task_cache[page_id] = notion_read("/pages/" + page_id)
            task = {key: property_value(value) for key, value in task_cache[page_id].get("properties", {}).items()}
            tasks.append(task)
        payload[destination] = [task.get("名前", "") for task in tasks]
        payload[destination.replace("tasks", "count")] = len(tasks)
        if destination == "done_tasks":
            payload["done_tasks_detail"] = [{"title": task.get("名前", ""), "done_date": task.get("Done date"), "event_date": task.get("Event Date")} for task in tasks]
    summary = parse_daily_log_payload(payload, target_date=target)
    if summary is None:
        raise ValueError("missing_evaluation_case")
    fields = build_diary_input_fields(summary)[0]
    return (target, fields, summary) if include_summary else (target, fields)


def select_cases(pages: list[dict]) -> list[tuple[str, dict]]:
    values = [(page, {key: property_value(prop) for key, prop in page.get("properties", {}).items()}) for page in pages]
    selected = []
    predicates = {
        "missing": lambda p: not p.get("Mood") and not p.get("Sleep Duration Min") and not p.get("Notes"),
        "trip_record": lambda p: "出張" in str(p.get("Location summary (GPT)") or ""),
        "late_record": lambda p: bool(re.search(r"深夜|夜遅|23時", str(p.get("Location summary (GPT)") or ""))),
        "task_changes": lambda p: len(p.get("Done Tasks") or []) + len(p.get("Drop Tasks") or []) >= 5,
        "study_workout": lambda p: (p.get("Study Minutes") or 0) > 0 and (p.get("Workout Duration Min") or 0) > 0,
    }
    for label, predicate in predicates.items():
        match = next((page for page, props in values if predicate(props) and all(page["id"] != prior[1]["id"] for prior in selected)), None)
        if match is not None:
            selected.append((label, match))
    return selected


def require_public_key():
    key = os.getenv("DIARY_EVAL_PUBLIC_KEY", "").strip()
    if not key:
        raise RuntimeError("evaluation_public_key_required_before_any_api_call")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    public = serialization.load_pem_public_key(key.encode())
    if not isinstance(public, RSAPublicKey) or public.key_size < 2048:
        raise ValueError("evaluation_requires_rsa_public_key_at_least_2048_bits")
    return public


def encrypt_private(payload: dict, path: Path) -> None:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    public = require_public_key()
    key, nonce = os.urandom(32), os.urandom(12)
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    ciphertext = AESGCM(key).encrypt(nonce, json.dumps(payload, ensure_ascii=False).encode(), b"diary-evaluation-v1")
    path.write_text(json.dumps({"key": base64.b64encode(wrapped).decode(), "nonce": base64.b64encode(nonce).decode(),
                               "ciphertext": base64.b64encode(ciphertext).decode()}))


def main() -> int:
    require_public_key()
    output = Path("artifacts/model_evaluation")
    output.mkdir(parents=True, exist_ok=True)
    maximum = min(1.50, float(os.getenv("DIARY_EVAL_MAX_USD", "1.50")))
    budget_used = 0.0
    private, report = {"cases": []}, {"results": [], "budget_limit_usd": maximum, "source_mutations": 0, "mails_sent": 0}
    pages = notion_read("/databases/" + os.environ["DAILY_LOG_DB_ID"] + "/query", {"page_size": 100, "sorts": [{"property": "Target Date", "direction": "descending"}]})["results"]
    task_cache = {}
    location_config = location_writer.load_config()
    location_client = location_writer.NotionClient(os.environ["NOTION_TOKEN"])
    for case_index, (label, page) in enumerate(select_cases(pages)):
        target, fields = case_fields(page, task_cache)
        start, end, _ = location_writer.compute_window_for_diary_date(target, location_config.tz, location_config.window_start_hour)
        location_payload = location_writer.collect_location_payload(location_client, location_config, start, end)
        if location_payload.get("main_sessions"):
            fields["Location Stays"] = json.dumps(location_payload["main_sessions"], ensure_ascii=False)
            fields["Location Window"] = json.dumps({"start": start.isoformat(), "end": end.isoformat()}, ensure_ascii=False)
        source = diary_sources(fields)
        system, user = _build_prompts(fields, target)
        location_system, location_user = location_writer.build_location_prompts(location_payload, location_client, location_config)
        private_case = {"index": case_index, "label": label, "target_date": target, "source_page": page, "fields": fields,
                        "location_payload": location_payload, "location_prompts": [location_system, location_user], "outputs": []}
        workloads = [
            ("diary", system, user, SENTENCE_FORMAT, ("gpt-4.1-mini", "gpt-6.1-terra")),
            ("location", location_system, location_user, None, ("gpt-4o-mini", "gpt-6.1-terra")),
            ("advice", "日本語で短い翌日への助言を書いてください。有効な気分記録が不足している場合は判定できないと明示し、新たな行動や高リスク判定・数値確率を創作しないでください。",
             json.dumps({"reflection_date": target, "valid_mood_targets": 0, "sources": source}, ensure_ascii=False), None, ("gpt-4.1-mini", "gpt-6.1-sol")),
            ("consistency", "日記の事実照合です。資料から証明できない主張を拒否してください。JSONでgrounded(boolean)とviolations(array)だけを返してください。",
             json.dumps({"sources": {**source, "payment_control": {"value": "飲食店への決済記録。購入品・本人の飲食・訪問は未確認。", "certainty": "recorded"}}, "sentences": [{"text": "飲食店の決済があるので、本人は食事をして一息ついた。", "sources": ["payment_control"], "certainty": "recorded"}]}, ensure_ascii=False),
             {"type": "json_object"}, ("gpt-4.1-mini", "gpt-6.1-sol")),
        ]
        if case_index == 0:
            assets = load_notes_prompt_assets()
            workloads.append(("extraction_synthetic_control", assets.get("system_prompt") or "メモから事実だけを抽出してJSONで返してください。未記録と推定を区別してください。",
                json.dumps({"rows": [{"id": "control1", "date": "2000-01-01", "text": "コーヒーを飲んだ。協議のタスクを完了に変更したが、実施日は未確認。"},
                                     {"id": "control2", "date": "2000-01-02", "text": ""}]}, ensure_ascii=False),
                {"type": "json_object"}, ("gpt-4.1-mini", "gpt-6-luna")))
        for role, system_prompt, user_prompt, response_format, models in workloads:
            for model in models:
                inp, cached, out = PRICES[model]
                bound = ((len((system_prompt + user_prompt).encode()) + 1024) * inp * 1.25 + 4096 * out) / 1_000_000 * 2
                record = {"case": case_index, "case_type": label, "role": role, "model": model}
                if budget_used + bound > maximum:
                    report["results"].append({**record, "status": "budget_not_run"})
                    continue
                usage = []
                started = time.perf_counter()
                text = ""
                saved_text = None
                try:
                    text = chat_completion(model=model, system_prompt=system_prompt, user_prompt=user_prompt, response_format=response_format, usage_sink=usage)
                    reasons = validate_sentences(json.loads(text).get("sentences"), source) if role == "diary" else []
                    if role == "location":
                        normalized = location_writer.normalize_diary_output(text)
                        projected = location_writer.build_summary_property(
                            {"properties": {"summary": {"type": "rich_text"}}}, "summary", normalized,
                        )
                        saved_text = "".join(item["text"]["content"] for item in projected["summary"]["rich_text"])
                        if not saved_text or saved_text != normalized:
                            reasons.append("location_storage_projection_mismatch")
                        record["postprocess_applied"] = True
                    if role == "consistency" and json.loads(text).get("grounded") is not False:
                        reasons.append("failed_to_reject_unsupported_payment_claim")
                    status = "rejected" if reasons else "generated_for_review"
                except Exception as exc:
                    reasons, status = [type(exc).__name__], "failed"
                known_cost = sum(((row.get("input_tokens") or 0) - (row.get("cached_tokens") or 0)) * inp
                    + (row.get("cached_tokens") or 0) * cached + (row.get("output_tokens") or 0) * out
                    + (row.get("cache_write_tokens") or 0) * inp * .25 for row in usage) / 1_000_000
                unknown = sum(not row.get("usage_known") for row in usage)
                budget_used += bound if unknown else known_cost
                report["results"].append({**record, "status": status, "reasons": reasons, "latency_ms": int((time.perf_counter()-started)*1000),
                    "usage": usage, "standard_price_estimate_usd": known_cost, "unknown_usage_attempts": unknown})
                private_case["outputs"].append({**record, "text": text, "saved_text": saved_text, "reasons": reasons})
        private["cases"].append(private_case)
    report.update(budget_accounted_usd=budget_used, cost_basis="公式Standard単価と実応答のusage。請求明細の実費ではない。使用量不明の試行は保守的上限で予算計上。",
                  input_scope="過去の保存記録と同日の位置原本。分類の空欄/状態変更は明示した合成対照、整合性は故意に誤りを入れた支払由来の主張。")
    encrypt_private(private, output / "private_outputs.encrypted.json")
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"evaluated_calls": len(report["results"]), "budget_accounted_usd": budget_used, "source_mutations": 0, "mails_sent": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
