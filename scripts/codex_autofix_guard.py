#!/usr/bin/env python3
import json, os, pathlib, sys
state_file = pathlib.Path('.codex/autofix_state.json')
key = os.getenv('AUTOFIX_KEY','unknown')
max_attempts = int(os.getenv('AUTOFIX_MAX_ATTEMPTS','2'))
state = {}
if state_file.exists():
    state = json.loads(state_file.read_text())
attempt = int(state.get(key,0)) + 1
state[key] = attempt
state_file.parent.mkdir(parents=True, exist_ok=True)
state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
print(f'attempt={attempt}')
print(f'max_attempts={max_attempts}')
if attempt > max_attempts:
    print('decision=stop')
    sys.exit(2)
print('decision=continue')
