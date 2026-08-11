# Daily automation reliability

## Root causes confirmed on 2026-08-11

- The Expenses query sent an unsupported `checkbox.is_empty` filter for `FamilyCard`. A read-only workspace check found known rows with `F=true` and `FamilyCard=false`, while the Daily Log snapshot recorded `query_failed`.
- Health pages existed after 2026-07-20 but could contain only `Date` and `Source`. HTTP success/page existence was therefore not evidence of usable health data.
- Today sleep selection accepted any valid historical candidate. The 374-minute sleep on 2026-07-20 was consequently rendered as 6.23 hours for 2026-08-10.
- F Risk calculated its high/medium decision before replacing the ML score with the rule fallback score.
- The bulk Daily Log history endpoint and Python mapper omitted most fields consumed by `build_daily_feature_table()`.
- Phase status represented exception handling, not semantic data quality.

## Implemented behavior

- Expense F uses `F.equals=true` and `FamilyCard.equals=false`. Errors expose only HTTP status, Notion error code, exception class, a bounded response message, and filter strategy.
- Health freshness uses `ok`, `no_data`, `stale`, `degraded`, and `failed` plus `data_date`, `last_valid_at`, `completeness`, `available_fields`, and `error_code`. Empty pages are not copied to Daily Log. Data absence is reported as a non-blocking warning, while transport/API failure remains blocking.
- Today sleep is limited to saved target-date properties or a candidate attributed to the target date by the canonical boundary. Historical sleep remains available only to trend calculations.
- F Risk order is ML scoring, scoring fallback, final score, risk level, then match. Query failures in historical Expense F data stop scoring as unavailable instead of becoming zero-event days.
- Phase C uses only `success`, `degraded`, `skipped`, and `failed`. `query_failed`, stale/no-data inputs, fallback, or skip reasons cannot become success.
- Repair evaluates `content_complete`, `source_complete`, and `analysis_complete` using the Daily Log, a live read-only Expense F query, and external F Risk state. Health `no_data/stale/degraded` is recorded as non-blocking `source_missing`; content and analysis repair continues without copying an older value. Critical Expense F or F Risk failures remain red. Historical mail defaults to off and scheduled repair never enables it.
- The read-only canary validates schemas, an F query, latest Health quality, and latest Daily Log read without printing merchant, note, location, or raw health values.

## Before / after

| Area | Before | After |
| --- | --- | --- |
| Expense F | Invalid checkbox filter could become `query_failed` while Phase C continued as success | Supported filter only; sanitized diagnostic fields; semantic degradation blocks success |
| Health | Page existence and HTTP success implied usable data | Major-field completeness and freshness determine `ok/no_data/stale/degraded/failed` |
| Today sleep | Any old valid sleep could be labeled as today | Only saved target-day or canonical-boundary match; otherwise no sleep number is rendered |
| F Risk | Fallback score was assigned after risk-level decisions | Final score is fixed before risk level and match; unavailable labels are never converted to zero events |
| History features | Bulk history discarded Notes, location, meal, task, expense, study, sleep and weather fields | Worker response and Python mapper preserve every feature group used by the model |
| Repair | Diary existence alone implied completeness; old values could be copied | Content/source/analysis completeness are independent; source absence is recorded, not fabricated |
| Mail | Manual repair defaulted to sending historical mail | Manual and scheduled repair default to no mail |
| Observability | Exception-free steps appeared successful | Semantic quality states, reason codes, hashes, generation time, run ID, fallback and ML-skip details are retained |

Validation for this change includes the complete Python suite, Worker unit tests, TypeScript type-checking, workflow contract checks, and read-only checks against the current Expenses, Health condition, and Daily Log data sources. No Notion writes were made during validation.

## Daily Signals schema proposal (no database is created by this change)

Daily Log remains the source of truth for historical facts. Predictions should be materialized into a separate `Daily Signals` data source after the schema is reviewed manually.

| Property | Type |
| --- | --- |
| Prediction Date | date (unique application key) |
| F Risk Score | number |
| F Risk Level | select: low / medium / high |
| F Risk Alert | rich_text |
| F Risk Reason | rich_text |
| F Risk Data Status | select: ok / no_data / stale / degraded / failed |
| F Risk Reason Code | rich_text |
| F Risk Input Hash | rich_text |
| F Risk Generated At | date-time |
| F Event Count | number |
| Fallback Used | checkbox |
| ML Skip Reason | rich_text |
| Today Advice | rich_text |
| Today Advice Input Hash | rich_text |
| Today Sleep Status | select: ok / no_data / stale / degraded / failed |
| Weather Forecast | rich_text |
| Source Freshness | rich_text containing redacted JSON |
| Source Status | rich_text containing redacted per-source status JSON |
| Model Name | rich_text |
| Run ID | rich_text |

Migration order: create schema manually, add dual-write behind a new explicit flag, verify readback for seven days, switch human views to Daily Signals, move machine state to Cloudflare D1 (preferred because it provides queryable dated rows, uniqueness constraints, and easier retention audits) or KV (acceptable for simple key lookup), then disable the `automation-state` writer. Do not rewrite Git history in this PR. After retention and backup are approved, remove the state branch using a separately reviewed procedure.

## Day boundaries

Python consumers use `scripts/day_key.py`. `CANONICAL_DAY_BOUNDARY_HOUR` defaults to 5 and a domain can override it with `<DOMAIN>_DAY_BOUNDARY_HOUR` (for example `SLEEP_DAY_BOUNDARY_HOUR`). The exact boundary belongs to the new day; one second before belongs to the previous day.

Worker consumers resolve the same canonical setting first and then apply `EXPENSES_DAY_START_HOUR`, `WINDOW_START_HOUR`, or `STUDY_DAY_START_HOUR` when explicitly configured. Study/Anki retains 04:00 because it defines an intentional study-session day, while sleep, expense, nutrition and location retain 05:00 because they attribute overnight or diary-window data. These are documented semantic overrides, not duplicate date arithmetic.

## Notion API migration inventory

The current database query endpoint is used in the Worker Daily Log/Inbox/Tasks/Health/location paths, the shared Worker Notion client, Expense F, weather location, voice diary notes, and the location summary writer. This PR deliberately restores the P0 query on the current API first. A later migration should resolve each database to its data source ID, add compatibility reads, migrate one domain at a time, and remove old endpoints only after the read-only canary passes on both implementations.

## Existing open PR disposition

- Already represented in current main: the schema audit and basic step-status/Expense F scaffolding from PRs #186-#190.
- Reimplemented on current main: PR #225 input-hash intent, with digests/redacted logging instead of raw Notes; PR #193's F Risk correctness intent, without merging its stale branch.
- Obsolete/closeable after this PR is accepted: duplicate old F Risk/schema branches #186-#190 and #193; #225 can close after confirming the expanded hash tests here.

## Security and rollback

CI must not print Notes, exact locations/addresses, merchants, raw health details, or the full behavioral-model input. Debug remains redacted. Rollback is a normal revert of this PR; no Notion schema mutation, repository visibility change, state-branch deletion, or history rewrite is performed here.

## Follow-up cleanup after acceptance

- Standardize upstream Health page titles to `Health | YYYY-MM-DD`; titles remain display-only and `Date` stays the application key.
- Remove deprecated save flags only after every workflow references the semantic Phase status and Daily Signals dual-write has completed its observation window.
- Close superseded F Risk/schema PRs after this replacement is merged; do not merge their branches.
- Run the Notion API migration inventory one domain at a time and keep the legacy query path only as a compatibility fallback until canary parity is proven.

## Enforced final quality gate

Daily Diary 04 keeps mail delivery before enforcement. Its final step reads the redacted quality artifact and fails the workflow when a critical source or analysis is not trustworthy. Missing or low-completeness Health and no target-date sleep candidate are warnings only, so they do not interrupt the daily chain. Critical failures still include missing/unavailable Expense F status, unreadable or missing F Risk state, degraded F Risk, fallback scoring, and missing F Risk observability metadata.

The GitHub step summary contains status names, completeness, field names, reason codes, counts, and hashes only. It does not contain Notes text, locations, merchants, raw Health values, or the mail body. A warning remains non-blocking by default; any error makes Daily Diary 04 red after the mail attempt.

The Notion read-only canary now runs after Daily Diary 04 completes, with a 14:30 JST scheduled fallback. It queries up to 50 recent Health pages so an empty latest page can still report the last page that contained a major Health field. The canary remains read-only and fails independently when the latest source quality is not `ok`.
