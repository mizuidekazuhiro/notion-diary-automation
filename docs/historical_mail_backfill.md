# Historical diary mail backfill

The `Daily Log Repair` workflow scans the requested JST date range and repairs missing or incomplete diary entries.

For manual `workflow_dispatch` runs, `send_mail` defaults to `true`. Each complete or newly repaired diary date is passed to the normal publish phase with its explicit `--target-date`. Existing mail hash/version metadata is respected, so an unchanged diary that was already sent is skipped by the normal deduplication logic.

Scheduled repair runs never pass `--send-mail`, preventing a daily resend of the preceding seven days.

Recommended manual settings:

- `days`: `7`
- `end_date`: blank to end at yesterday JST
- `dry_run`: `false`
- `send_mail`: `true`
