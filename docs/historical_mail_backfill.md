# Historical diary mail backfill

The `Daily Log Repair` workflow scans the requested JST date range and repairs missing or incomplete diary entries.

For manual `workflow_dispatch` runs, `send_mail` defaults to `false`. Only an explicit `send_mail=true` opt-in passes complete or newly repaired diary dates to the normal publish phase with their explicit `--target-date`. Existing mail hash/version metadata is respected, so an unchanged diary that was already sent is skipped by the normal deduplication logic.

Scheduled repair runs never pass `--send-mail`, preventing a daily resend of the preceding seven days.

Recommended manual settings:

- `days`: `7`
- `end_date`: blank to end at yesterday JST
- `dry_run`: `false`
- `send_mail`: `false` (change to `true` only when historical delivery is intentional)
