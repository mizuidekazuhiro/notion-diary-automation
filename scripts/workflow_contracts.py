from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
README = ROOT / "README.md"

EXPECTED_CHAIN = {
    "Daily Diary 02 - Generate Location Summary": "Daily Diary 01 - Ingest Daily Log",
    "Daily Diary 03 - Generate Diary & Sleep Insights": "Daily Diary 02 - Generate Location Summary",
    "Daily Diary 04 - Publish Daily Mail": "Daily Diary 03 - Generate Diary & Sleep Insights",
}
CI_WORKFLOW_NAME = "CI - Test & Requirements Gate"
INGEST_CRON = 'cron: "0 3 * * *"'
REPAIR_CRON = 'cron: "0 5 * * *"'
CANARY_CRON = 'cron: "30 5 * * *"'


def _read_workflow(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_workflow_name(content: str) -> str:
    match = re.search(r"^name:\s*(.+)$", content, flags=re.MULTILINE)
    if not match:
        raise ValueError("workflow name is missing")
    return match.group(1).strip().strip('"').strip("'")


def extract_workflow_run_sources(content: str) -> list[str]:
    match = re.search(
        r"workflow_run:\s*\n\s*workflows:\s*\n((?:\s*-\s*.+\n)+)",
        content,
    )
    if not match:
        return []
    return [
        line.strip()[2:].strip().strip('"').strip("'")
        for line in match.group(1).splitlines()
        if line.strip().startswith("-")
    ]


def check_workflow_chain() -> list[str]:
    issues: list[str] = []
    name_to_sources: dict[str, list[str]] = {}

    for workflow_file in WORKFLOW_DIR.glob("*.yml"):
        content = _read_workflow(workflow_file)
        name = extract_workflow_name(content)
        name_to_sources[name] = extract_workflow_run_sources(content)

    for workflow_name, expected_source in EXPECTED_CHAIN.items():
        sources = name_to_sources.get(workflow_name)
        if sources is None:
            issues.append(f"missing_workflow={workflow_name}")
            continue
        if expected_source not in sources:
            issues.append(
                "workflow_dependency_mismatch "
                f"workflow={workflow_name} "
                f"expected_source={expected_source} "
                f"actual_sources={sources}"
            )

    deploy_sources = name_to_sources.get("Deploy Cloudflare Workers", [])
    if CI_WORKFLOW_NAME not in deploy_sources:
        issues.append(
            "deploy_workers must depend on CI workflow via workflow_run for pre-deploy safety"
        )

    readme_text = README.read_text(encoding="utf-8")
    for workflow_name in [*EXPECTED_CHAIN.keys(), *EXPECTED_CHAIN.values()]:
        if workflow_name not in readme_text:
            issues.append(f"README missing workflow name: {workflow_name}")

    repair = WORKFLOW_DIR / "repair_daily_logs.yml"
    if not repair.exists():
        issues.append("missing repair_daily_logs workflow")
    else:
        repair_text = repair.read_text(encoding="utf-8")
        for needle, msg in [
            ("workflow_dispatch:", "repair workflow missing workflow_dispatch"),
            (REPAIR_CRON, "repair workflow missing 0 5 * * * cron"),
            ("concurrency:", "repair workflow missing concurrency"),
            ("if: always()", "repair workflow must always summarize/upload artifacts"),
            ("actions/upload-artifact", "repair workflow missing artifact upload"),
            (
                "python scripts/backfill_missing_diaries.py",
                "repair workflow missing backfill command",
            ),
            ("send_mail:", "repair workflow missing send_mail input"),
            ('default: "false"', "repair workflow send_mail must default to false"),
            ("--send-mail", "repair workflow missing historical mail flag"),
            (
                "github.event_name",
                "repair workflow must guard historical mail by event type",
            ),
            ("GMAIL_APP_PASSWORD", "repair workflow missing mail credentials"),
        ]:
            if needle not in repair_text:
                issues.append(msg)
        if (
            "DAILY_LOG_REPAIR_ENABLED" in repair_text
            or "vars.DAILY_LOG_REPAIR_ENABLED" in repair_text
        ):
            issues.append(
                "repair workflow schedule must run without repository variable guard"
            )
        repair_step = re.search(
            r"- name: Repair Daily Logs(?P<body>.*?)(?:\n\s+- name:|\Z)",
            repair_text,
            flags=re.S,
        )
        if repair_step and "continue-on-error" in repair_step.group("body"):
            issues.append("repair command must not use continue-on-error")
        if repair_step:
            repair_body = repair_step.group("body")
            if "workflow_dispatch" not in repair_body or "--send-mail" not in repair_body:
                issues.append(
                    "historical mail must be enabled only inside the manual repair step"
                )

    ingest = WORKFLOW_DIR / "ingest_daily_log.yml"
    if not ingest.exists():
        issues.append("missing ingest_daily_log workflow")
    else:
        ingest_text = ingest.read_text(encoding="utf-8")
        if "workflow_dispatch:" not in ingest_text:
            issues.append("ingest workflow missing workflow_dispatch")
        if INGEST_CRON not in ingest_text:
            issues.append("ingest workflow missing 0 3 * * * cron")
        if "backfill_missing_diaries.py" in ingest_text:
            issues.append("old ingest workflow still contains backfill step")

    publish = WORKFLOW_DIR / "publish_daily_mail.yml"
    if not publish.exists():
        issues.append("missing publish_daily_mail workflow")
    else:
        publish_text = publish.read_text(encoding="utf-8")
        for needle, msg in [
            ("Enforce final daily quality gate", "publish workflow missing final quality gate"),
            ("python scripts/enforce_daily_quality_gate.py", "publish workflow missing quality gate command"),
            ("--report artifacts/daily_mail/quality_report.json", "publish quality gate missing redacted report input"),
            ("if: always()", "publish quality gate must run after earlier failures"),
        ]:
            if needle not in publish_text:
                issues.append(msg)
        gate_step = re.search(
            r"- name: Enforce final daily quality gate(?P<body>.*?)(?:\n\s+- name:|\Z)",
            publish_text,
            flags=re.S,
        )
        if gate_step and "continue-on-error" in gate_step.group("body"):
            issues.append("final daily quality gate must not use continue-on-error")

    canary = WORKFLOW_DIR / "notion_readonly_canary.yml"
    if not canary.exists():
        issues.append("missing notion_readonly_canary workflow")
    else:
        canary_text = canary.read_text(encoding="utf-8")
        for needle, msg in [
            ("Daily Diary 04 - Publish Daily Mail", "canary must run after Daily Diary 04"),
            (CANARY_CRON, "canary workflow missing 30 5 * * * fallback cron"),
            ("permissions:\n  contents: read", "canary must remain read-only"),
            ("python scripts/notion_readonly_canary.py", "canary workflow missing validator command"),
        ]:
            if needle not in canary_text:
                issues.append(msg)

    return issues


def main() -> None:
    issues = check_workflow_chain()
    if issues:
        for item in issues:
            print(f"ERROR: {item}")
        raise SystemExit(1)
    print("Workflow contracts verified.")


if __name__ == "__main__":
    main()
