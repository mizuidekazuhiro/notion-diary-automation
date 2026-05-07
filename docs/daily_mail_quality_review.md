# Daily mail quality review

Phase D exports a privacy-conscious quality report for the rendered Daily Log mail after `python scripts/daily_job.py --phase publish` runs.

## Default artifacts

The workflow writes these artifacts under `artifacts/daily_mail`:

- `quality_report.json`
- `quality_report.md`

When review is needed, it also prepares:

- `issue_title.txt`
- `issue_body.md`

The report stores section presence, lengths, issue codes, and suggested fix areas. It does **not** store the full mail body because this repository may be public and the mail can include diary, sleep, health, study, and other private data.

## Issue behavior

If the quality report is `warning` or `fail`, the workflow creates or comments on a GitHub Issue titled:

```text
[Daily Mail Quality] <target_date> needs review
```

The issue body includes a Codex task prompt, but it does not auto-merge any changes.

To create Issues only for hard failures, set this repository variable:

```text
DAILY_MAIL_QUALITY_CREATE_ISSUE_ON=fail
```

Valid values:

- `warning`
- `fail`

The default is `warning`.

## Current checks

The quality report checks:

- rendered plain text / HTML are present
- `Today advice` exists and is rendered
- `Today advice` length is within the configured range
- `Today advice` appears to use recent/historical trend evidence
- `Today advice` does not rely only on sleep language
- generic phrases such as `無理せず` are flagged
- Study data exists but the study section is missing
- Sleep data exists but the sleep section is missing
- Weather data exists but the weather section is missing
- Diary exists but is not rendered
- Meal summary exists but is not rendered

## Configurable Today advice length

Defaults follow the current README design:

```text
DAILY_MAIL_TODAY_ADVICE_MIN_CHARS=220
DAILY_MAIL_TODAY_ADVICE_MAX_CHARS=380
```

Override them as repository variables or workflow environment variables if the output policy changes.

## Safety boundaries

This implementation intentionally does not:

- send the mail again during quality export
- auto-merge code changes
- save full rendered mail bodies
- include diary, sleep, health, or study details in the GitHub Issue body
- read Gmail directly

The quality export reuses the existing Daily Log read endpoint and mail renderer to check whether the rendered output includes the expected sections.
