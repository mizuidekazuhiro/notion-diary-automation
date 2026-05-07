# Daily mail quality review

Phase D can export a privacy-conscious quality report for the rendered Daily Log mail after `python scripts/daily_job.py --phase publish` runs.

## Default behavior

The workflow writes these artifacts under `artifacts/daily_mail`:

- `quality_report.json`
- `quality_report.md`

The report stores section presence, lengths, issue codes, and suggested fix areas. It does **not** store the full mail body by default because this repository may be public and the mail can include diary, sleep, health, study, and other private data.

If the quality report is `warning` or `fail`, the workflow creates or comments on a GitHub Issue titled:

```text
[Daily Mail Quality] <target_date> needs review
```

The issue body includes a Codex task prompt, but it does not auto-merge any changes.

## Optional full-body artifacts

Set this repository variable only if you explicitly accept storing the rendered mail body as a GitHub Actions artifact:

```text
DAILY_MAIL_ARTIFACT_INCLUDE_BODY=true
```

When enabled, the workflow also writes:

- `mail_plain_text.redacted.txt`
- `mail_html.redacted.html`

Mail action tokens in links are redacted, but the mail content itself can still contain private information.

## Issue threshold

By default, GitHub Issues are created for `warning` and `fail` reports.

To create Issues only for hard failures, set:

```text
DAILY_MAIL_QUALITY_CREATE_ISSUE_ON=fail
```

Valid values:

- `warning`
- `fail`

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
