"""Read-only production verification. No mutation, generation, or SMTP."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit, urlunsplit, urlencode
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingest.http_client import fetch_json
from scripts.daily_job import load_config
from scripts.ai_models import ROLE_DEFAULTS, model_for, request_settings
from publish.read_daily_log import parse_daily_log_payload
from publish.render_mail import render_mail
from scripts.mail_preflight import preflight_report


def main():
    config = load_config(need_mail=False, need_tasks=False)
    parts = urlsplit(config.daily_log_read_url)
    health = fetch_json(urlunsplit((parts.scheme, parts.netloc, '/health', '', '')), config.bearer_token)
    expected = os.environ.get('EXPECTED_WORKER_SHA') or os.environ.get('GITHUB_SHA')
    if health.get('version') != expected:
        raise RuntimeError('deployed_commit_mismatch')
    end = (datetime.now(ZoneInfo('Asia/Tokyo')).date() - timedelta(days=1)).isoformat()
    start = (datetime.fromisoformat(end).date() - timedelta(days=29)).isoformat()
    url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/') + '/history', urlencode({'start': start, 'end': end}), ''))
    started = time.perf_counter()
    history = fetch_json(url, config.bearer_token)
    elapsed = int((time.perf_counter() - started) * 1000)
    rows = history.get('items')
    if not isinstance(rows, list) or not rows:
        raise RuntimeError('production_history_unavailable')
    summaries = [parse_daily_log_payload({**row, 'found': True}, target_date=row['target_date']) for row in rows]
    inspected = []
    for summary in summaries:
        if not summary or not summary.diary_evidence_json:
            continue
        expense = {'matched': False, 'data_status': summary.expense_f_data_status or 'expenses_data_unavailable'}
        risk = {'matched': False, 'data_status': 'read_only_unassessed'}
        mail = render_mail(summary, expense_f_alert=expense, f_risk_alert=risk)
        report = preflight_report(summary, mail, expense_f=expense, f_risk=risk, run_id=os.getenv('GITHUB_RUN_ID', 'local'))
        inspected.append({'case': len(inspected), 'error_count': report['error_count'], 'warning_count': report['warning_count']})
    report = {'worker': health, 'checkout_sha': os.getenv('GITHUB_SHA'),
        'inspection_scope': {'diary_evidence_and_render': 'checked', 'f_risk_prediction': 'not_evaluated', 'smtp': 'not_invoked'},
        'effective_models': {role: {'model': model_for(role), 'settings': request_settings(model_for(role))} for role in ROLE_DEFAULTS},
        'history_fetches': 1, 'history_rows': len(rows), 'history_elapsed_ms': elapsed,
        'grounded_diaries_checked': inspected, 'source_mutations': 0, 'mails_sent': 0}
    output = Path('artifacts/production_check'); output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    if len(inspected) < int(os.getenv('MIN_GROUNDED_DIARIES', '1')):
        raise RuntimeError('no_persisted_diaries_verified')
    if any(row['error_count'] for row in inspected):
        raise RuntimeError('persisted_diary_preflight_failed')


if __name__ == '__main__':
    main()
