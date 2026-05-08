#!/usr/bin/env python3
import json, os, subprocess, pathlib

def sh(cmd):
    return subprocess.check_output(cmd, shell=True, text=True).strip()
run_id = os.getenv('TARGET_RUN_ID','')
pr_number = os.getenv('TARGET_PR_NUMBER','')
ctx = {
  'run_id': run_id,
  'pr_number': pr_number,
  'target_branch': os.getenv('TARGET_BRANCH',''),
  'workflow_name': os.getenv('TARGET_WORKFLOW_NAME',''),
  'job_name': os.getenv('TARGET_JOB_NAME',''),
}
try:
    ctx['recent_diff'] = sh('git --no-pager diff --stat HEAD~1..HEAD || true')
except Exception:
    ctx['recent_diff'] = ''
ctx['test_stack'] = 'pytest / scripts/verify_requirements.py / scripts/workflow_contracts.py'
readme = pathlib.Path('README.md')
ctx['readme_summary'] = readme.read_text()[:4000] if readme.exists() else ''
pathlib.Path('.codex').mkdir(exist_ok=True)
pathlib.Path('.codex/failure_context.json').write_text(json.dumps(ctx, ensure_ascii=False, indent=2))
print('.codex/failure_context.json')
