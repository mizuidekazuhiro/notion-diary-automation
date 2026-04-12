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


def _read_workflow(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_workflow_name(content: str) -> str:
    match = re.search(r"^name:\s*(.+)$", content, flags=re.MULTILINE)
    if not match:
        raise ValueError("workflow name is missing")
    return match.group(1).strip().strip('"').strip("'")


def extract_workflow_run_sources(content: str) -> list[str]:
    match = re.search(r"workflow_run:\s*\n\s*workflows:\s*\n((?:\s*-\s*.+\n)+)", content)
    if not match:
        return []
    return [line.strip()[2:].strip().strip('"').strip("'") for line in match.group(1).splitlines() if line.strip().startswith("-")]


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
                f"workflow_dependency_mismatch workflow={workflow_name} expected_source={expected_source} actual_sources={sources}"
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
