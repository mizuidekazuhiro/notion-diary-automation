"""Read-only repair preview: encrypted originals and generated replacements, no mail."""
from __future__ import annotations
import dataclasses
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_diary_models import notion_read, property_value, case_fields, encrypt_private, require_public_key
from apps.location_summary_writer.src import main as location_writer
from scripts.diary_generator import generate_diary_result
from scripts.daily_job import _build_diary_hash_payload, _build_input_hash
from scripts.ai_models import model_for


def main():
    require_public_key()
    end = (datetime.now(ZoneInfo('Asia/Tokyo')).date() - timedelta(days=1)).isoformat()
    start = (datetime.fromisoformat(end).date() - timedelta(days=6)).isoformat()
    pages = notion_read('/databases/' + os.environ['DAILY_LOG_DB_ID'] + '/query', {
        'page_size': 20, 'filter': {'and': [
            {'property': 'Target Date', 'date': {'on_or_after': start}},
            {'property': 'Target Date', 'date': {'on_or_before': end}},
        ]}, 'sorts': [{'property': 'Target Date', 'direction': 'ascending'}]})['results']
    if len(pages) > 7:
        raise RuntimeError('ambiguous_duplicate_repair_dates')
    cfg = location_writer.load_config()
    client = location_writer.NotionClient(os.environ['NOTION_TOKEN'])
    task_cache, prepared = {}, []
    for page in pages:
        if not property_value(page['properties'].get('Diary', {})):
            continue
        target, fields, summary = case_fields(page, task_cache, include_summary=True)
        window_start, window_end, _ = location_writer.compute_window_for_diary_date(target, cfg.tz, cfg.window_start_hour)
        payload = location_writer.collect_location_payload(client, cfg, window_start, window_end)
        if payload.get('main_sessions'):
            fields['Location Stays'] = json.dumps(payload['main_sessions'], ensure_ascii=False)
            fields['Location Window'] = json.dumps({'start': window_start.isoformat(), 'end': window_end.isoformat()}, ensure_ascii=False)
        prepared.append((page, target, fields, summary, payload))
    # Reserve both output caps plus conservative UTF-8 input bounds before any AI call.
    bound = sum(((len(json.dumps(fields, ensure_ascii=False).encode()) + 6000) * (1.25 + 2.5)
                 + 4096 * 15) / 1_000_000 * 2 for _, _, fields, _, _ in prepared)
    if bound > 1.50:
        raise RuntimeError('repair_preview_budget_bound_exceeded')
    private, statuses = {'repairs': []}, []
    for page, target, fields, summary, location_payload in prepared:
        result = generate_diary_result(fields, target)
        input_hash, _, _ = _build_input_hash(_build_diary_hash_payload(summary, fields)[0])
        evidence = {**result.evidence, 'source_page_id': page['id'], 'input_hash': input_hash,
                    'text_hash': hashlib.sha256(result.text.strip().encode()).hexdigest()}
        advice_day = (datetime.fromisoformat(target) + timedelta(days=1)).date().isoformat()
        if summary.mood is not None or summary.sleep_duration_min is not None:
            raise RuntimeError('repair_advice_requires_individual_evidence_review')
        advice = f'{advice_day}への助言：気分や睡眠の記録が不足しているため、体調やリスクは判定できません。'
        generation = {'diary': {'status': result.status, 'method': result.method, 'fallback_used': result.fallback_used,
            'rejection_reasons': result.rejection_reasons, 'model': result.model, 'consistency_model': model_for('consistency'),
            'target_date': target, 'input_hash': input_hash, 'prompt_version': result.prompt_version},
            'advice': {'status': 'insufficient_data', 'method': 'deterministic', 'fallback_used': False,
            'target_date': advice_day, 'model': None, 'prompt_version': 'repair-evidence-v1', 'rejection_reasons': ['missing_mood_and_sleep']}}
        private['repairs'].append({'date': target, 'page_id': page['id'], 'before': page,
            'fields': fields, 'location_payload': location_payload, 'replacement': {
                'Diary': result.text, 'Today advice': advice, 'Diary Input Hash': input_hash,
                'Diary Evidence JSON': json.dumps(evidence, ensure_ascii=False),
                'Generation Metadata': json.dumps(generation, ensure_ascii=False)}, 'result': dataclasses.asdict(result)})
        statuses.append({'case': len(statuses), 'status': result.status, 'method': result.method, 'reasons': result.rejection_reasons})
    output = Path('artifacts/repair_preview')
    output.mkdir(parents=True, exist_ok=True)
    encrypt_private(private, output / 'repair_preview.encrypted.json')
    (output / 'report.json').write_text(json.dumps({'statuses': statuses, 'reserved_max_usd': bound,
        'source_mutations': 0, 'mails_sent': 0}), encoding='utf-8')
    print(json.dumps({'cases': len(statuses), 'statuses': statuses, 'source_mutations': 0, 'mails_sent': 0}))


if __name__ == '__main__':
    main()
