#!/usr/bin/env python3
import json
import os
import subprocess
from typing import Optional


def count_from_comments(pr_number: str, repo: str) -> int:
    if not pr_number:
        return 0
    try:
        out = subprocess.check_output([
            'gh', 'api', f'repos/{repo}/issues/{pr_number}/comments', '--paginate'
        ], text=True)
        comments = json.loads(out)
        return sum(1 for c in comments if '[codex-autofix-attempt]' in (c.get('body') or ''))
    except Exception:
        return 0


def run_guard(max_attempts: int, pr_number: str, run_id: str, key: str, repo: str, output_path: Optional[str]) -> int:
    attempt = count_from_comments(pr_number, repo) + 1
    can_fix = attempt <= max_attempts
    print(f'attempt={attempt}')
    print(f'max_attempts={max_attempts}')
    print(f'key={key}')
    print(f'run_id={run_id}')
    print(f'can_fix={str(can_fix).lower()}')

    if output_path:
      with open(output_path, 'a', encoding='utf-8') as f:
          f.write(f'attempt={attempt}\n')
          f.write(f'can_fix={str(can_fix).lower()}\n')
    return 0 if can_fix else 2


if __name__ == '__main__':
    code = run_guard(
        max_attempts=int(os.getenv('AUTOFIX_MAX_ATTEMPTS', '2')),
        pr_number=os.getenv('TARGET_PR_NUMBER', '').strip(),
        run_id=os.getenv('TARGET_RUN_ID', '').strip(),
        key=os.getenv('AUTOFIX_KEY', 'unknown'),
        repo=os.getenv('GITHUB_REPOSITORY', ''),
        output_path=os.getenv('GITHUB_OUTPUT')
    )
    raise SystemExit(code)
