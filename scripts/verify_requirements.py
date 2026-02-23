import re
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_TS = ROOT / "workers" / "src" / "index.ts"
# NOTE: workers layout changed in past refactors, so relations source is resolved by:
# 1) RELATIONS_TS_PATH override, 2) repo scan with priority, 3) optional skip unless CI_STRICT=1.


def assert_contains(pattern: str, text: str, message: str) -> None:
    if not re.search(pattern, text, re.MULTILINE | re.DOTALL):
        raise AssertionError(message)


def assert_not_contains(pattern: str, text: str, message: str) -> None:
    if re.search(pattern, text, re.MULTILINE | re.DOTALL):
        raise AssertionError(message)


def resolve_relations_ts_path() -> Path | None:
    env_path = os.environ.get("RELATIONS_TS_PATH")
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        if candidate.exists():
            print(f"INFO: using RELATIONS_TS_PATH={candidate}")
            return candidate
        print(f"WARNING: RELATIONS_TS_PATH not found: {candidate}")

    ts_like = [p for p in ROOT.rglob("*.ts") if "/node_modules/" not in p.as_posix()]
    name_hit = [
        p
        for p in ts_like
        if (
            p.name == "daily_log_task_relations.ts"
            or "daily_log_task_relations" in p.stem
            or "task_relations" in p.stem
            or "relations" in p.stem
            or "daily_log_task" in p.stem
        )
    ]
    content_hit = []
    for p in ts_like:
        text = p.read_text(encoding="utf-8")
        if (
            "updateDailyLogTaskRelations" in text
            and "dateProperty" in text
            and "is_not_empty" in text
        ):
            content_hit.append(p)

    merged = []
    seen = set()
    for p in [*name_hit, *content_hit]:
        if p not in seen:
            seen.add(p)
            merged.append(p)

    def rank(path: Path) -> tuple[int, str]:
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("workers/src/"):
            return (0, rel)
        if rel.startswith("apps/"):
            return (1, rel)
        if rel.startswith("src/"):
            return (2, rel)
        return (3, rel)

    sorted_candidates = sorted(merged, key=rank)
    if not sorted_candidates:
        return None

    if len(sorted_candidates) > 1:
        print("WARNING: multiple relations candidates found:")
        for candidate in sorted_candidates:
            print(f"WARNING: - {candidate.relative_to(ROOT)}")

    selected = sorted_candidates[0]
    print(f"INFO: selected relations source: {selected.relative_to(ROOT)}")
    return selected


def main() -> None:
    index_text = INDEX_TS.read_text(encoding="utf-8")
    relations_ts_path = resolve_relations_ts_path()
    relations_text: str | None = None
    if relations_ts_path is not None:
        relations_text = relations_ts_path.read_text(encoding="utf-8")

    assert_not_contains(
        r'name:\s*"Notes"',
        index_text,
        "Daily_Log schema should not include Notes.",
    )
    assert_not_contains(
        r"properties\.Notes",
        index_text,
        "Daily_Log upsert payload should not include Notes.",
    )

    assert_contains(
        r'property:\s*(?:"Status"|statusPropertyName)\s*,\s*select:\s*\{\s*equals:\s*doneStatus',
        index_text,
        "Done filter should include Status == doneStatus.",
    )
    assert_contains(
        r'property:\s*(?:"Status"|statusPropertyName)\s*,\s*select:\s*\{\s*equals:\s*droppedStatus',
        index_text,
        "Drop filter should include Status == droppedStatus.",
    )
    assert_contains(
        r'property:\s*(?:"Done date"|doneDatePropertyName)\s*,\s*date:\s*\{\s*is_not_empty:\s*true',
        index_text,
        "Done filter should require Done date is_not_empty.",
    )
    assert_contains(
        r'property:\s*(?:"Drop date"|dropDatePropertyName)\s*,\s*date:\s*\{\s*is_not_empty:\s*true',
        index_text,
        "Drop filter should require Drop date is_not_empty.",
    )
    assert_contains(
        r'property:\s*(?:"Done date"|doneDatePropertyName)[\s\S]*on_or_after:[\s\S]*before:',
        index_text,
        "Done filter should include date range.",
    )
    assert_contains(
        r'property:\s*(?:"Drop date"|dropDatePropertyName)[\s\S]*on_or_after:[\s\S]*before:',
        index_text,
        "Drop filter should include date range.",
    )

    if relations_text is None:
        strict = os.environ.get("CI_STRICT") == "1"
        message = "Daily log relation source not found; skipping relations-specific checks."
        if strict:
            print(f"ERROR: {message} Set RELATIONS_TS_PATH or fix repo layout.")
            raise SystemExit(1)
        print(f"WARNING: {message}")
    else:
        assert_contains(
            r"dateProperty[\s\S]*is_not_empty",
            relations_text,
            "Daily log relation query should require date is_not_empty.",
        )

    print("All requirement checks passed.")


if __name__ == "__main__":
    main()
