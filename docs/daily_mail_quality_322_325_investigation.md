# Daily Mail Quality #322–#325 investigation

Inspected on 2026-10-05 at main `e8dad656e083e98b7b9c4311f217992fc97b2183`.
All four publish runs and their upstream Phase A/C runs used this same commit.
No newer repair PR was returned by the repository PR search at investigation time.

| Issue | Daily Log date (JST) | Publish run | Compact advice chars |
| --- | --- | --- | --- |
| #322 | 2026-10-01 | 36991061639 | 201 |
| #323 | 2026-10-02 | 37112008460 | 210 |
| #324 | 2026-10-03 | 37192799304 | 194 |
| #325 | 2026-10-04 | 37295854473 | 194 |

Each report has Errors 0, Warnings 3: `health_no_data`,
`today_sleep_no_data`, `today_advice_length_out_of_range`. All six monitored
mail sections are present. There are no duplicate Daily Logs in the publish
diagnostics. Phase C loaded 30 history records, with zero failed/missing reads.

## Missing Health and sleep: shared upstream condition

Phase A runs 36990315382, 37111528021, 37192392276, 37295017297 report
`status=no_data`, `error_code=major_fields_empty`, `available_fields=[]`,
`completeness=0`, and `last_valid_at=2026-09-09T14:40:00.000Z`.
The `data_date` matches each target date; this is not a logged date mismatch.

Independent read-only canary runs 36999754334, 37116629183, 37197902569,
37309631298 confirm `page_found=true` and the same empty major Health fields.
The Health schema checks succeed (33 properties). Phase C runs 36990428631,
37111591988, 37192446607, 37295133351 each examine eight sleep candidates
and select none (`no_valid_candidate`, `missing_required_sleep_data`).

The Worker reads the target-date Health page and deliberately does not ingest
`no_data` or stale values. The connector keeps other sources running, and
the quality report preserves both missing-data warnings. No rendering failure
or read failure is evidenced for these two warnings.

The reason the source pages contain no major Health values is **unknown**.
These logs cannot distinguish an inactive sender, failed sender request,
empty payload, or a source-side mapping problem. `last_valid_at` is the last
valid record timestamp found by the existing query, not proof of the last
sender invocation. Next inspect the Health sender's execution/HTTP response
and Worker ingest logs for these dates. Never copy older values into them.

## Short advice: separate generation contract defect

Phase C generated and saved advice on all four dates (`updated=true`). Its
logged final text lengths exactly match the publish quality measurements.
The runs log `today_advice_fallback_used=False`: the short texts were accepted
model outputs, not the missing-sleep fallback. Sparse input is a contributing
context, but the deterministic code defect is independent of Health ingestion.

The renderer asks for 260–420 characters while the quality checker expects
220–380 compact characters. It does not validate output length. The input-hash
cache can also reuse out-of-range advice indefinitely when inputs do not change.

The fix shares the quality checker's compact-length limits with the renderer,
uses the existing fallback on out-of-range generation, and regenerates cached
advice with invalid length even when the hash is unchanged. Phase C and Repair
workflows receive the same optional limit variables as Phase D. No historical
Health values, Notion schema, warning thresholds, workflow success policy,
or automatic merge settings are changed. No production repair or mail resend
was triggered during this investigation.

## Validation

- 76 related tests passed: new length regressions, mail quality, 30-day advice,
  Health freshness, sleep resolution, and Health ingest quality gate.
- 3 Phase C cache tests passed: valid cached advice is reused, invalid cached
  advice is regenerated, and changed inputs still regenerate.
- Workflow contracts and Python compilation passed.
- Four incident lengths, lower/upper boundaries, whitespace counting,
  custom 240–360 limits, and exception/no-pattern fallback paths covered.
- The missing Health/sleep warnings remain in the quality report after the
  advice warning is removed. Tests use synthetic text, not personal mail bodies.

Local tests use the available Python 3.12 runtime and existing dependency
packages. The production CI uses Python 3.11 and pinned requirements; its full
result must be checked on the PR. The local full suite is not claimed as passed.

## Evidence links

- https://github.com/mizuidekazuhiro/notion-diary-automation/issues/325
- https://github.com/mizuidekazuhiro/notion-diary-automation/issues/324
- https://github.com/mizuidekazuhiro/notion-diary-automation/issues/323
- https://github.com/mizuidekazuhiro/notion-diary-automation/issues/322
- https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/37295017297
- https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/37295133351
- https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/37295854473
- https://github.com/mizuidekazuhiro/notion-diary-automation/actions/runs/37309631298
